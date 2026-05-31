"""Tests for agent registry slot resolution."""
import pytest

from app.agent_registry import (
    ORCHESTRATOR_SLOT,
    SPECIALIST_SLOT,
    resolve_node_agent,
    resolve_specialist,
)
from app.database import SessionLocal
from app.tests.conftest import agent_payload


@pytest.mark.asyncio
async def test_resolve_specialist_trip_planner(client, seeded_agents):
    await client.post("/agents", json=agent_payload(
        id="agent_trip_planner",
        name="Trip Planner",
        role="Travel itinerary specialist",
        skills="trip planning, flights, hotels",
    ))

    db = SessionLocal()
    try:
        agent = resolve_specialist(db, "Plan a 5-day trip to Tokyo", "direct_answer")
        assert agent is not None
        assert agent.id == "agent_trip_planner"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_resolve_node_agent_specialist_slot(client, seeded_agents):
    await client.post("/agents", json=agent_payload(
        id="agent_trip_planner",
        name="Trip Planner",
        role="Travel planner",
        skills="itinerary",
    ))

    db = SessionLocal()
    try:
        state = {}
        node = {"id": "specialist", "type": "agent", "agent_slot": SPECIALIST_SLOT}
        agent = resolve_node_agent(
            db, node, user_prompt="Plan a trip to Paris", runtime_kind="direct_answer", state=state,
        )
        assert agent.id == "agent_trip_planner"
        assert state["routed_specialist_id"] == "agent_trip_planner"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_resolve_node_agent_orchestrator_slot(client, seeded_agents):
    db = SessionLocal()
    try:
        node = {"id": "router", "type": "agent", "agent_slot": ORCHESTRATOR_SLOT}
        agent = resolve_node_agent(
            db, node, user_prompt="hello", runtime_kind="direct_answer", state={},
        )
        assert agent.id == "agent_router"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_finance_after_trip_conversation_routes_to_reasoning_not_trip(client, seeded_agents):
    """New finance topic in same conv_id must not reuse Trip Planner from prior turns."""
    await client.post("/agents", json=agent_payload(
        id="agent_trip_planner",
        name="Trip planner",
        role="Travel planner",
        skills="trip planning, itineraries",
    ))

    from app.agent_runtime import build_conversation_context
    from app.database import MessageLogModel

    db = SessionLocal()
    try:
        wf_id = "conv_routing_test"
        db.add_all([
            MessageLogModel(
                workflow_id=wf_id,
                sender="Human (Web)",
                content="help me with a 3day trip plan to dharmashala?",
            ),
            MessageLogModel(
                workflow_id=wf_id,
                sender="Trip planner",
                content="Here is your Dharamshala itinerary...",
            ),
            MessageLogModel(
                workflow_id=wf_id,
                sender="Human (Web)",
                content="i want to do paragliding as well?",
            ),
        ])
        db.commit()

        current = "1000 rs invested at 10% annual rate what it will be after 3 yrs?"
        context = build_conversation_context(db, wf_id, current)
        agent = resolve_specialist(
            db, context, "direct_answer", current_prompt=current,
        )
        assert agent is not None
        assert agent.id != "agent_trip_planner"
        assert agent.id == "agent_specialist"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_trip_follow_up_still_uses_trip_planner(client, seeded_agents):
    await client.post("/agents", json=agent_payload(
        id="agent_trip_planner",
        name="Trip planner",
        role="Travel planner",
        skills="trip planning",
    ))

    from app.agent_runtime import build_conversation_context
    from app.database import MessageLogModel

    db = SessionLocal()
    try:
        wf_id = "conv_follow_up"
        db.add_all([
            MessageLogModel(
                workflow_id=wf_id,
                sender="Human (Web)",
                content="Plan a 3-day trip to Dharamshala",
            ),
            MessageLogModel(
                workflow_id=wf_id,
                sender="Trip planner",
                content="Day 1: Mall Road...",
            ),
        ])
        db.commit()

        current = "i want to do paragliding as well?"
        context = build_conversation_context(db, wf_id, current)
        agent = resolve_specialist(
            db, context, "direct_answer", current_prompt=current,
        )
        assert agent.id == "agent_trip_planner"
    finally:
        db.close()
