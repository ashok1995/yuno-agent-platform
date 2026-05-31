import datetime
import logging

from app.logging_config import setup_logging

setup_logging()

from fastapi import FastAPI, Depends, BackgroundTasks, WebSocket, WebSocketDisconnect, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session
from app.database import (
    SessionLocal,
    AgentModel,
    MessageLogModel,
    WorkflowDefinitionModel,
    DATABASE_FILE,
    seed_database,
    sync_agent_models,
    ensure_default_agents,
    mark_default_agent_deleted,
)
from app.workflow_definitions import (
    BUILTIN_TEMPLATES,
    list_all_templates,
    get_template,
    to_mermaid,
)
from app.engine import ws_manager
from app.graph import execute_workflow_pipeline
from app.channel import start_telegram_channel_polling
from app.router_service import OrchestrationRouterService
from app.event_store import get_events, clear_events   # ← NEW
from sqlalchemy import distinct

logger = logging.getLogger("yuno.runtime")

PROTECTED_AGENT_IDS = frozenset({
    "agent_router",
    "agent_specialist",
    "agent_code_reviewer",
})

app = FastAPI(title="Yuno Core Agent Engine Runtime")


@app.on_event("startup")
def startup_event():
    seed_database()
    ensure_default_agents()
    sync_agent_models()
    start_telegram_channel_polling()
    db = SessionLocal()
    try:
        count = db.query(AgentModel).count()
        logger.info(f"Loaded {count} agent(s) from SQLite: {DATABASE_FILE}")
        print(f"✅ Agent registry: {count} profile(s) persisted at {DATABASE_FILE}")
    finally:
        db.close()
    logger.info("Yuno Platform initialized: Database seeded and Telegram listener online.")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ─── PYDANTIC SCHEMAS ──────────────────────────────────────────────────────────

class WorkflowRequest(BaseModel):
    template_id: str
    workflow_id: str
    user_prompt: str


class AgentSchema(BaseModel):
    id: str
    name: str
    role: str
    system_prompt: str
    model: str
    tools: str
    channels: str
    schedules: str
    memory_window: int
    skills: str
    interaction_rules: str
    guardrails: str

    model_config = ConfigDict(from_attributes=True)


class WorkflowDefinitionSchema(BaseModel):
    id: str
    name: str
    description: str = ""
    runtime_template: str
    definition_json: str


def _resolve_runtime_template(template_id: str, db: Session) -> str:
    """Map UI template id (builtin or custom saved) to graph.py runtime branch."""
    if template_id in BUILTIN_TEMPLATES:
        return template_id
    custom = db.query(WorkflowDefinitionModel).filter(
        WorkflowDefinitionModel.id == template_id
    ).first()
    if custom:
        return custom.runtime_template
    return template_id


# ─── AGENT LIFECYCLE MANAGEMENT ENDPOINTS (10-DIM CRUD) ───────────────────────

from app.tools import list_tools


def _agent_to_response(agent: AgentModel) -> dict:
    data = AgentSchema.model_validate(agent).model_dump()
    data["created_at"] = (
        agent.created_at.isoformat() if getattr(agent, "created_at", None) else None
    )
    data["updated_at"] = (
        agent.updated_at.isoformat() if getattr(agent, "updated_at", None) else None
    )
    return data


@app.get("/tools")
def get_tools():
    """List registered agent tools with category and description."""
    return {"tools": list_tools()}


@app.get("/agents/meta")
def get_agents_meta(db: Session = Depends(get_db)):
    """Storage info for UI — agents persist in SQLite across restarts."""
    count = db.query(AgentModel).count()
    return {
        "storage": "sqlite",
        "database_path": str(DATABASE_FILE),
        "agent_count": count,
        "protected_ids": sorted(PROTECTED_AGENT_IDS),
    }


@app.get("/agents")
def get_agents(db: Session = Depends(get_db)):
    agents = db.query(AgentModel).order_by(AgentModel.name).all()
    return [_agent_to_response(a) for a in agents]


@app.get("/agents/{agent_id}")
def get_agent(agent_id: str, db: Session = Depends(get_db)):
    agent = db.query(AgentModel).filter(AgentModel.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
    return _agent_to_response(agent)


@app.post("/agents")
def upsert_agent(payload: AgentSchema, db: Session = Depends(get_db)):
    agent = db.query(AgentModel).filter(AgentModel.id == payload.id).first()
    is_new = agent is None
    if is_new:
        agent = AgentModel(id=payload.id)
        db.add(agent)
    for k, v in payload.model_dump().items():
        setattr(agent, k, v)
    agent.updated_at = datetime.datetime.utcnow()
    if is_new and not getattr(agent, "created_at", None):
        agent.created_at = agent.updated_at
    db.commit()
    db.refresh(agent)
    logger.info(f"Agent {'created' if is_new else 'updated'}: {payload.id}")
    return {"status": "success", "agent": _agent_to_response(agent)}


@app.delete("/agents/{agent_id}")
def delete_agent(agent_id: str, db: Session = Depends(get_db)):
    if agent_id in PROTECTED_AGENT_IDS:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete built-in platform agents.",
        )
    agent = db.query(AgentModel).filter(AgentModel.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
    db.delete(agent)
    db.commit()
    mark_default_agent_deleted(agent_id)
    logger.info(f"Agent deleted: {agent_id}")
    return {"status": "deleted", "id": agent_id}


# ─── EVENT POLLING ENDPOINTS (replaces WebSocket for Streamlit) ───────────────

@app.get("/events")
def poll_events():
    """
    Streamlit frontend polls this on every rerun to drain buffered agent events.
    Returns all events written since the last clear.
    """
    return {"events": get_events()}


@app.delete("/events")
def flush_events():
    """
    Called by the frontend after WORKFLOW_COMPLETE to reset the buffer
    so the next workflow run starts clean.
    """
    clear_events()
    return {"status": "cleared"}


# ─── WORKFLOW DEFINITIONS (visual builder API) ────────────────────────────────

@app.get("/workflows/templates")
def list_workflow_templates(db: Session = Depends(get_db)):
    """Built-in templates plus user-saved custom workflow graphs."""
    templates = list_all_templates()
    custom_rows = db.query(WorkflowDefinitionModel).all()
    for row in custom_rows:
        templates.append({
            "id": row.id,
            "name": row.name,
            "description": row.description,
            "runtime_template": row.runtime_template,
            "feedback_loop": False,
            "custom": True,
            "nodes": [],
            "edges": [],
        })
    return templates


@app.get("/workflows/templates/{template_id}")
def get_workflow_template(template_id: str, db: Session = Depends(get_db)):
    builtin = get_template(template_id)
    if builtin:
        return {**builtin, "mermaid": to_mermaid(builtin), "custom": False}

    custom = db.query(WorkflowDefinitionModel).filter(
        WorkflowDefinitionModel.id == template_id
    ).first()
    if not custom:
        raise HTTPException(status_code=404, detail="Template not found")

    import json
    definition = json.loads(custom.definition_json)
    definition["id"] = custom.id
    definition["name"] = custom.name
    definition["description"] = custom.description
    definition["runtime_template"] = custom.runtime_template
    definition["custom"] = True
    definition["mermaid"] = to_mermaid(definition)
    return definition


@app.get("/workflows/templates/{template_id}/mermaid")
def get_workflow_mermaid(template_id: str, db: Session = Depends(get_db)):
    detail = get_workflow_template(template_id, db)
    return {"template_id": template_id, "mermaid": detail["mermaid"]}


@app.post("/workflows/definitions")
def save_workflow_definition(payload: WorkflowDefinitionSchema, db: Session = Depends(get_db)):
    """Save or update a custom workflow graph from the visual builder."""
    if payload.runtime_template not in BUILTIN_TEMPLATES:
        raise HTTPException(
            status_code=400,
            detail=f"runtime_template must be one of: {', '.join(BUILTIN_TEMPLATES.keys())}",
        )
    row = db.query(WorkflowDefinitionModel).filter(
        WorkflowDefinitionModel.id == payload.id
    ).first()
    if not row:
        row = WorkflowDefinitionModel(id=payload.id)
        db.add(row)
    row.name = payload.name
    row.description = payload.description
    row.runtime_template = payload.runtime_template
    row.definition_json = payload.definition_json
    db.commit()
    return {"status": "saved", "id": payload.id}


@app.delete("/workflows/definitions/{definition_id}")
def delete_workflow_definition(definition_id: str, db: Session = Depends(get_db)):
    if definition_id in BUILTIN_TEMPLATES:
        raise HTTPException(status_code=400, detail="Cannot delete built-in templates.")
    row = db.query(WorkflowDefinitionModel).filter(
        WorkflowDefinitionModel.id == definition_id
    ).first()
    if not row:
        return {"status": "not_found"}
    db.delete(row)
    db.commit()
    return {"status": "deleted"}


# ─── AUTONOMOUS WORKFLOW ORCHESTRATION PIPELINE ───────────────────────────────

async def _run_workflow_job(template_id: str, workflow_id: str, user_prompt: str) -> None:
    """
    Background job: resolve routing (may call Ollama) then execute the graph.
    Kept off the HTTP thread so POST /workflows/run returns immediately.
    """
    from app.agent_runtime import build_conversation_context
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        routing_context = build_conversation_context(db, workflow_id, user_prompt)
    finally:
        db.close()

    try:
        if template_id == "dynamic_router_intent":
            execution_template = await OrchestrationRouterService.resolve_topology(
                user_prompt=routing_context,
                requested_template=template_id,
                current_prompt=user_prompt,
            )
        else:
            execution_template = template_id

        await execute_workflow_pipeline(execution_template, workflow_id, user_prompt)
    except Exception as e:
        logger.error(f"Workflow job failed for {workflow_id}: {e}")


@app.post("/workflows/run", status_code=status.HTTP_202_ACCEPTED)
async def trigger_workflow(payload: WorkflowRequest, background_tasks: BackgroundTasks):
    """
    Accepts global specifications and queues routing + execution in the background.
    Returns 202 immediately so the UI can start polling /events without blocking on Ollama.
    """
    logger.info(f"Incoming task footprint: Run ID [{payload.workflow_id}]")

    try:
        background_tasks.add_task(
            _run_workflow_job,
            payload.template_id,
            payload.workflow_id,
            payload.user_prompt,
        )
    except Exception as queue_err:
        logger.critical(f"Task queue failure on active cluster allocation: {queue_err}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Local agent compute cluster queue allocation failure.",
        )

    # Static templates are known upfront; dynamic routing resolves in the background.
    if payload.template_id == "dynamic_router_intent":
        resolved_hint = "routing"
    else:
        resolved_hint = payload.template_id

    return {
        "status": "queued",
        "workflow_id": payload.workflow_id,
        "resolved_template_target": resolved_hint,
    }


# ─── REAL-TIME WEBSOCKET (kept for optional future use) ───────────────────────

@app.websocket("/ws/logs")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
        
        
# ─── PERSISTED MESSAGE LOGS ───────────────────────────────────────────────────

@app.get("/messages")
def get_messages(workflow_id: str = None, limit: int = 100, db: Session = Depends(get_db)):
    """
    Fetch persisted inter-agent message logs.
    Optional ?workflow_id= filter. Returns newest-first.
    """
    query = db.query(MessageLogModel)
    if workflow_id:
        query = query.filter(MessageLogModel.workflow_id == workflow_id)
    rows = query.order_by(MessageLogModel.id.asc()).limit(limit).all()
    return [
        {
            "id": r.id,
            "workflow_id": r.workflow_id,
            "sender": r.sender,
            "content": r.content,
        }
        for r in rows
    ]


@app.get("/messages/workflows")
def get_distinct_workflows(db: Session = Depends(get_db)):
    """
    Returns the list of distinct workflow IDs that have persisted messages.
    Used by the UI history tab to populate the selector.
    """
    
    rows = db.query(distinct(MessageLogModel.workflow_id)).all()
    return [r[0] for r in rows if r[0]]