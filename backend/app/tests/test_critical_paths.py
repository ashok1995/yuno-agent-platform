"""
Critical-path API tests: agent CRUD, workflow trigger, events, message persistence.

Run from backend/:
    PYTHONPATH=. pytest app/tests/ -v
"""
import pytest

from app.database import SessionLocal, MessageLogModel
from app.event_store import push_event
from app.tests.conftest import agent_payload


# ── 1. Agent CRUD ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_agent(client):
    resp = await client.post("/agents", json=agent_payload())
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"


@pytest.mark.asyncio
async def test_create_agent_persists_to_db(client):
    await client.post("/agents", json=agent_payload(id="persist_test", name="Persist Agent"))
    resp = await client.get("/agents")
    assert resp.status_code == 200
    ids = [a["id"] for a in resp.json()]
    assert "persist_test" in ids


@pytest.mark.asyncio
async def test_upsert_agent_updates_existing(client):
    await client.post("/agents", json=agent_payload(name="Original Name"))
    await client.post("/agents", json=agent_payload(name="Updated Name"))
    resp = await client.get("/agents")
    agents = [a for a in resp.json() if a["id"] == "test_agent_01"]
    assert len(agents) == 1
    assert agents[0]["name"] == "Updated Name"


@pytest.mark.asyncio
async def test_delete_agent(client):
    await client.post("/agents", json=agent_payload())
    del_resp = await client.delete("/agents/test_agent_01")
    assert del_resp.status_code == 200
    assert del_resp.json()["status"] == "deleted"

    resp = await client.get("/agents")
    ids = [a["id"] for a in resp.json()]
    assert "test_agent_01" not in ids


@pytest.mark.asyncio
async def test_delete_nonexistent_agent(client):
    resp = await client.delete("/agents/does_not_exist")
    assert resp.status_code == 404


# ── 2. Workflow execution trigger ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_workflow_trigger_accepted(client, seeded_agents):
    resp = await client.post("/workflows/run", json={
        "template_id": "direct_answer",
        "workflow_id": "test_wf_001",
        "user_prompt": "What is the capital of France?",
    })
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "queued"
    assert body["workflow_id"] == "test_wf_001"
    assert body["resolved_template_target"] == "direct_answer"


@pytest.mark.asyncio
async def test_workflow_dynamic_template_returns_routing_hint(client, seeded_agents):
    resp = await client.post("/workflows/run", json={
        "template_id": "dynamic_router_intent",
        "workflow_id": "test_wf_dyn",
        "user_prompt": "hello",
    })
    assert resp.status_code == 202
    assert resp.json()["resolved_template_target"] == "routing"


@pytest.mark.asyncio
async def test_workflow_trigger_returns_resolved_template(client, seeded_agents):
    resp = await client.post("/workflows/run", json={
        "template_id": "code_review_loop",
        "workflow_id": "test_wf_002",
        "user_prompt": "Review this: x = eval(input())",
    })
    assert resp.status_code == 202
    assert resp.json()["resolved_template_target"] == "code_review_loop"


# ── 3. Event store (live delivery to UI poll endpoint) ─────────────────────────

@pytest.mark.asyncio
async def test_events_endpoint_empty_on_start(client):
    resp = await client.get("/events")
    assert resp.status_code == 200
    assert resp.json()["events"] == []


@pytest.mark.asyncio
async def test_events_endpoint_returns_pushed_events(client):
    push_event({"type": "AGENT_START", "data": {"agent_name": "Test Agent"}})
    push_event({"type": "WORKFLOW_COMPLETE", "data": {"status": "completed"}})

    resp = await client.get("/events")
    events = resp.json()["events"]
    assert len(events) == 2
    assert events[0]["type"] == "AGENT_START"
    assert events[1]["type"] == "WORKFLOW_COMPLETE"


@pytest.mark.asyncio
async def test_events_cleared_after_delete(client):
    push_event({"type": "TOKEN_STREAM", "data": {"token": "hello"}})

    del_resp = await client.delete("/events")
    assert del_resp.status_code == 200

    resp = await client.get("/events")
    assert resp.json()["events"] == []


# ── 4. Message persistence ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_messages_endpoint_empty_initially(client):
    resp = await client.get("/messages")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_messages_saved_and_retrieved(client):
    db = SessionLocal()
    try:
        db.add(MessageLogModel(
            workflow_id="wf_test_123",
            sender="Reasoning Specialist",
            content="Paris is the capital of France.",
        ))
        db.commit()
    finally:
        db.close()

    resp = await client.get("/messages")
    msgs = resp.json()
    assert len(msgs) == 1
    assert msgs[0]["sender"] == "Reasoning Specialist"
    assert msgs[0]["workflow_id"] == "wf_test_123"


@pytest.mark.asyncio
async def test_messages_filtered_by_workflow_id(client):
    db = SessionLocal()
    try:
        db.add(MessageLogModel(workflow_id="wf_aaa", sender="Router", content="msg A"))
        db.add(MessageLogModel(workflow_id="wf_bbb", sender="Router", content="msg B"))
        db.commit()
    finally:
        db.close()

    resp = await client.get("/messages", params={"workflow_id": "wf_aaa"})
    msgs = resp.json()
    assert len(msgs) == 1
    assert msgs[0]["workflow_id"] == "wf_aaa"


@pytest.mark.asyncio
async def test_distinct_workflow_ids_endpoint(client):
    db = SessionLocal()
    try:
        db.add(MessageLogModel(workflow_id="wf_x1", sender="A", content="hello"))
        db.add(MessageLogModel(workflow_id="wf_x2", sender="B", content="world"))
        db.add(MessageLogModel(workflow_id="wf_x1", sender="A", content="again"))
        db.commit()
    finally:
        db.close()

    resp = await client.get("/messages/workflows")
    assert set(resp.json()) == {"wf_x1", "wf_x2"}
