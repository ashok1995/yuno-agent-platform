"""Agent CRUD persistence tests (SQLite-backed)."""
import pytest

from app.database import AgentModel, SessionLocal
from app.tests.conftest import agent_payload


@pytest.mark.asyncio
async def test_agents_meta_endpoint(client, seeded_agents):
    resp = await client.get("/agents/meta")
    assert resp.status_code == 200
    data = resp.json()
    assert data["storage"] == "sqlite"
    assert data["agent_count"] >= 3
    assert "agent_router" in data["protected_ids"]


@pytest.mark.asyncio
async def test_agent_persists_after_create(client, seeded_agents):
    payload = agent_payload(
        id="agent_trip_persist",
        name="Trip Planner",
        role="Travel assistant",
        skills="trip planning, itineraries",
        system_prompt="You plan trips.",
    )
    r = await client.post("/agents", json=payload)
    assert r.status_code == 200

    listed = await client.get("/agents")
    ids = [a["id"] for a in listed.json()]
    assert "agent_trip_persist" in ids

    # Simulate restart: new DB session still sees the row
    db = SessionLocal()
    try:
        row = db.query(AgentModel).filter(AgentModel.id == "agent_trip_persist").first()
        assert row is not None
        assert row.name == "Trip Planner"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_agent_update(client, seeded_agents):
    await client.post("/agents", json=agent_payload(
        id="agent_updatable",
        name="Original Name",
        system_prompt="v1",
    ))
    updated = agent_payload(id="agent_updatable", name="Updated Name", system_prompt="v2")
    r = await client.post("/agents", json=updated)
    assert r.status_code == 200
    assert r.json()["agent"]["name"] == "Updated Name"

    one = await client.get("/agents/agent_updatable")
    assert one.json()["system_prompt"] == "v2"


@pytest.mark.asyncio
async def test_cannot_delete_builtin_agent(client, seeded_agents):
    r = await client.delete("/agents/agent_router")
    assert r.status_code == 400

    db = SessionLocal()
    try:
        assert db.query(AgentModel).filter(AgentModel.id == "agent_router").first()
    finally:
        db.close()


@pytest.mark.asyncio
async def test_delete_custom_agent(client, seeded_agents):
    await client.post("/agents", json=agent_payload(id="agent_temp", name="Temp"))
    r = await client.delete("/agents/agent_temp")
    assert r.status_code == 200

    listed = await client.get("/agents")
    ids = [a["id"] for a in listed.json()]
    assert "agent_temp" not in ids


@pytest.mark.asyncio
async def test_delete_trip_planner_stays_deleted_after_restart(client, seeded_agents):
    """Optional demo agent must not be re-created by ensure_default_agents on startup."""
    from app.database import ensure_default_agents, SessionLocal

    await client.post("/agents", json=agent_payload(
        id="agent_trip_planner",
        name="Trip Planner",
        role="Travel specialist",
        skills="trip planning, itineraries",
        channels="web,telegram",
    ))

    r = await client.delete("/agents/agent_trip_planner")
    assert r.status_code == 200

    ensure_default_agents()

    db = SessionLocal()
    try:
        row = db.query(AgentModel).filter(AgentModel.id == "agent_trip_planner").first()
        assert row is None
    finally:
        db.close()

    listed = await client.get("/agents")
    ids = [a["id"] for a in listed.json()]
    assert "agent_trip_planner" not in ids
