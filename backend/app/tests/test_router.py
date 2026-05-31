"""Tests for Qwen-based routing and registry-backed specialist selection."""
import pytest

from app.router_service import OrchestrationRouterService


@pytest.mark.asyncio
async def test_resolve_topology_passes_through_static_template():
    result = await OrchestrationRouterService.resolve_topology(
        "any prompt", "financial_analysis"
    )
    assert result == "financial_analysis"


@pytest.mark.asyncio
async def test_qwen_routes_direct_answer_on_ollama_failure(monkeypatch):
    async def broken_collect(model, prompt):
        raise ConnectionError("Ollama unavailable")

    monkeypatch.setattr(
        "app.router_service.call_ollama_collect", broken_collect
    )

    result = await OrchestrationRouterService.resolve_topology(
        "What is the capital of France?", "dynamic_router_intent"
    )
    assert result == "direct_answer"


@pytest.mark.asyncio
async def test_greeting_routes_to_direct_answer_without_ollama():
    result = await OrchestrationRouterService.resolve_topology(
        "Hi how are you",
        "dynamic_router_intent",
        current_prompt="Hi how are you",
    )
    assert result == "direct_answer"


def test_is_small_talk_detects_greetings():
    assert OrchestrationRouterService._is_small_talk("Hi")
    assert OrchestrationRouterService._is_small_talk("Hi how are you")
    assert OrchestrationRouterService._is_small_talk("Hello!")
    assert not OrchestrationRouterService._is_small_talk(
        "Plan a 5-day trip to Tokyo"
    )


@pytest.mark.asyncio
async def test_trip_query_routes_to_direct_answer_workflow(client, seeded_agents):
    """Router picks workflow; registry picks trip planner agent at execution."""
    from app.tests.conftest import agent_payload

    await client.post("/agents", json=agent_payload(
        id="agent_trip_planner",
        name="Trip Planner",
        role="Travel itinerary specialist",
        skills="trip planning, flights, hotels, destinations",
    ))

    result = await OrchestrationRouterService.resolve_topology(
        "Plan a 5-day trip to Tokyo with flights and hotels",
        "dynamic_router_intent",
    )
    assert result == "direct_answer"


@pytest.mark.asyncio
async def test_trip_planner_resolved_via_registry_in_pipeline(client, seeded_agents, mock_llm):
    """Generic specialist node runs trip planner from registry, not hardcoded agent."""
    from app.tests.conftest import agent_payload, wait_for_workflow_complete

    await client.post("/agents", json=agent_payload(
        id="agent_trip_planner",
        name="Trip Planner",
        role="Travel specialist",
        skills="itinerary planning",
    ))

    wf_id = "wf_trip_registry"
    await client.post("/workflows/run", json={
        "template_id": "direct_answer",
        "workflow_id": wf_id,
        "user_prompt": "Plan a weekend in London",
    })
    await wait_for_workflow_complete(client)

    msgs = (await client.get("/messages", params={"workflow_id": wf_id})).json()
    senders = {m["sender"] for m in msgs}
    assert "Trip Planner" in senders
