from app.database import SessionLocal, MessageLogModel, USER_SENDER_WEB
from app.agent_runtime import resolve_workflow_definition
from app.engine import ws_manager
from app.workflow_definitions import get_template
from app.workflow_executor import execute_workflow_from_definition


async def _complete(wf_status: str = "completed"):
    try:
        await ws_manager.broadcast({
            "type": "WORKFLOW_COMPLETE",
            "data": {"status": wf_status},
        })
    except Exception as e:
        print(f"[graph] Failed to broadcast WORKFLOW_COMPLETE: {e}")


async def _persist_user_message(db, workflow_id: str, prompt: str, channel: str) -> None:
    """Save the user's prompt so persisted messages show the full conversation."""
    from app.database import USER_SENDER_TELEGRAM

    sender = USER_SENDER_TELEGRAM if channel == "telegram" else USER_SENDER_WEB
    db.add(MessageLogModel(workflow_id=workflow_id, sender=sender, content=prompt))
    db.commit()
    await ws_manager.broadcast({
        "type": "USER_MESSAGE",
        "data": {"sender": sender, "content": prompt, "workflow_id": workflow_id},
    })


async def execute_workflow_pipeline(
    template_id: str,
    workflow_id: str,
    prompt: str,
    channel: str = "web",
):
    db = SessionLocal()
    try:
        definition = resolve_workflow_definition(template_id, db)
        if not definition:
            definition = get_template("direct_answer")
        if not definition:
            print(f"[graph] Unknown template {template_id}")
            await _complete("error_unknown_template")
            return

        from app.database import AgentModel

        if not db.query(AgentModel).first():
            print(f"[graph] No agents in registry for {workflow_id}")
            await _complete("error_missing_agents")
            return

        await _persist_user_message(db, workflow_id, prompt, channel)
        await execute_workflow_from_definition(
            definition, db, workflow_id, prompt, channel=channel,
        )
        await _complete("completed")

    except Exception as e:
        print(f"[graph] Pipeline crash on {workflow_id}: {e}")
        db.rollback()
        await _complete("completed_with_errors")

    finally:
        db.close()
