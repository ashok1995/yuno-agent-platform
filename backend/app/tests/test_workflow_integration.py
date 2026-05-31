"""
Integration tests: workflow execution, event delivery, and message persistence.

Ollama is mocked via the mock_llm fixture — no GPU or local model required.
"""
import pytest

from app.database import SessionLocal, MessageLogModel
from app.tests.conftest import agent_payload, wait_for_workflow_complete


@pytest.mark.asyncio
async def test_financial_workflow_persists_specialist_message(
    client, seeded_agents, mock_llm
):
    """financial_analysis: tool runs + specialist output saved to DB."""
    wf_id = "wf_fin_integration"
    prompt = "1000 invested at 5% annual return for 5 yrs"

    resp = await client.post("/workflows/run", json={
        "template_id": "financial_analysis",
        "workflow_id": wf_id,
        "user_prompt": prompt,
    })
    assert resp.status_code == 202

    events = await wait_for_workflow_complete(client)
    assert any(e["type"] == "TOOL_EXECUTION" for e in events)
    assert any(e["type"] == "WORKFLOW_COMPLETE" for e in events)

    msgs_resp = await client.get("/messages", params={"workflow_id": wf_id})
    msgs = msgs_resp.json()
    assert len(msgs) >= 2
    assert msgs[0]["sender"] == "Human (Web)"
    agent_msgs = [
        m for m in msgs
        if m["sender"] not in ("Human (Web)", "Orchestrator Router")
    ]
    assert agent_msgs, "Expected at least one specialist agent message"
    assert "Mock output" in agent_msgs[0]["content"]


@pytest.mark.asyncio
async def test_financial_workflow_math_tool_contains_future_value(
    client, seeded_agents, mock_llm
):
    """math_evaluator should compute FV before specialist runs."""
    wf_id = "wf_fin_math"
    prompt = "1000 invested at 5% return annual what it will be after 5 yrs?"

    await client.post("/workflows/run", json={
        "template_id": "financial_analysis",
        "workflow_id": wf_id,
        "user_prompt": prompt,
    })
    events = await wait_for_workflow_complete(client)

    tool_events = [
        e for e in events
        if e.get("type") == "TOOL_EXECUTION"
        and e.get("data", {}).get("tool") in ("math_solver", "math_evaluator")
    ]
    assert tool_events, "Expected math_evaluator TOOL_EXECUTION event"
    assert "1276.28" in tool_events[0]["data"]["result"]


@pytest.mark.asyncio
async def test_code_review_workflow_persists_specialist_report(
    client, seeded_agents, mock_llm
):
    """code_review_loop: routing tool + security tools + specialist report (no router LLM answer)."""
    wf_id = "wf_code_integration"
    prompt = "Review: user_input = eval(input())"

    await client.post("/workflows/run", json={
        "template_id": "code_review_loop",
        "workflow_id": wf_id,
        "user_prompt": prompt,
    })
    events = await wait_for_workflow_complete(client)

    tools = {e["data"]["tool"] for e in events if e.get("type") == "TOOL_EXECUTION"}
    assert "security_scanner" in tools
    assert "code_reviewer" in tools
    assert "Orchestrator Router" in tools

    msgs = (await client.get("/messages", params={"workflow_id": wf_id})).json()
    senders = {m["sender"] for m in msgs}
    assert "Human (Web)" in senders
    assert "Orchestrator Router" not in senders
    assert "Code Review Specialist" in senders or "Reasoning Specialist" in senders


@pytest.mark.asyncio
async def test_direct_answer_workflow_single_specialist_message(
    client, seeded_agents, mock_llm
):
    wf_id = "wf_direct_integration"
    await client.post("/workflows/run", json={
        "template_id": "direct_answer",
        "workflow_id": wf_id,
        "user_prompt": "What is 2+2?",
    })
    await wait_for_workflow_complete(client)

    msgs = (await client.get("/messages", params={"workflow_id": wf_id})).json()
    assert len(msgs) == 2
    assert msgs[0]["sender"] == "Human (Web)"
    assert msgs[1]["sender"] == "Reasoning Specialist"


@pytest.mark.asyncio
async def test_dynamic_router_financial_end_to_end(client, seeded_agents, mock_llm, monkeypatch):
    """dynamic_router_intent uses Qwen routing → financial_analysis (mocked)."""

    async def fake_resolve(user_prompt, requested_template, **kwargs):
        from app.engine import ws_manager
        if requested_template == "dynamic_router_intent":
            await ws_manager.broadcast({
                "type": "TOOL_EXECUTION",
                "data": {
                    "tool": "Orchestrator Router (Qwen)",
                    "result": "Intent classified → [financial_analysis]",
                },
            })
            return "financial_analysis"
        return requested_template

    monkeypatch.setattr(
        "app.main.OrchestrationRouterService.resolve_topology",
        fake_resolve,
    )

    wf_id = "wf_dynamic_fin"
    resp = await client.post("/workflows/run", json={
        "template_id": "dynamic_router_intent",
        "workflow_id": wf_id,
        "user_prompt": "1000 invested at 5% annual for 5 years",
    })
    assert resp.status_code == 202

    events = await wait_for_workflow_complete(client)
    router_tools = [
        e for e in events
        if e.get("type") == "TOOL_EXECUTION"
        and "Orchestrator Router" in e.get("data", {}).get("tool", "")
    ]
    assert router_tools
    assert "financial_analysis" in router_tools[0]["data"]["result"]


@pytest.mark.asyncio
async def test_workflow_without_agents_emits_complete(client, mock_llm):
    """Missing DB agents should still emit WORKFLOW_COMPLETE (error path)."""
    wf_id = "wf_no_agents"
    await client.post("/workflows/run", json={
        "template_id": "direct_answer",
        "workflow_id": wf_id,
        "user_prompt": "hello",
    })
    events = await wait_for_workflow_complete(client)
    assert any(e["type"] == "WORKFLOW_COMPLETE" for e in events)

    msgs = (await client.get("/messages", params={"workflow_id": wf_id})).json()
    assert msgs == []


@pytest.mark.asyncio
async def test_code_review_feedback_loop_on_critical(
    client, seeded_agents, mock_llm
):
    """CRITICAL scan findings trigger feedback_loop event and extra agent messages."""
    wf_id = "wf_feedback_loop"
    prompt = "def run(): return eval(input())"

    await client.post("/workflows/run", json={
        "template_id": "code_review_loop",
        "workflow_id": wf_id,
        "user_prompt": prompt,
    })
    events = await wait_for_workflow_complete(client)

    loop_events = [
        e for e in events
        if e.get("type") == "TOOL_EXECUTION"
        and e.get("data", {}).get("tool") == "feedback_loop"
    ]
    assert loop_events, "Expected feedback_loop TOOL_EXECUTION for CRITICAL code"

    msgs = (await client.get("/messages", params={"workflow_id": wf_id})).json()
    router_msgs = [m for m in msgs if m["sender"] == "Orchestrator Router"]
    assert len(router_msgs) == 0, "Orchestrator should route only — no persisted answers"
    specialist_msgs = [
        m for m in msgs
        if m["sender"] in ("Code Review Specialist", "Reasoning Specialist")
    ]
    assert len(specialist_msgs) >= 2, "Feedback loop should produce a revised specialist report"


@pytest.mark.asyncio
async def test_workflow_persists_user_message(client, seeded_agents, mock_llm):
    wf_id = "wf_user_msg"
    await client.post("/workflows/run", json={
        "template_id": "direct_answer",
        "workflow_id": wf_id,
        "user_prompt": "What is the capital of France?",
    })
    await wait_for_workflow_complete(client)

    msgs = (await client.get("/messages", params={"workflow_id": wf_id})).json()
    assert msgs[0]["sender"] == "Human (Web)"
    assert "France" in msgs[0]["content"]
    assert any(m["sender"] == "Reasoning Specialist" for m in msgs)


@pytest.mark.asyncio
async def test_custom_workflow_feedback_loop_flag(client, seeded_agents, monkeypatch):
    """Custom saved workflow with feedback_loop=true triggers loop on CRITICAL."""
    import json

    definition = {
        "id": "custom_feedback_test",
        "runtime_template": "code_review_loop",
        "feedback_loop": True,
        "nodes": [
            {"id": "router", "type": "agent", "label": "Router", "agent_id": "agent_router"},
            {"id": "scanner", "type": "tool", "label": "scan", "tool": "security_scanner"},
            {"id": "specialist", "type": "agent", "label": "Reviewer", "agent_id": "agent_code_reviewer"},
        ],
        "edges": [
            {"from": "start", "to": "router", "condition": "always", "label": "Begin"},
            {"from": "router", "to": "scanner", "condition": "always", "label": "Scan"},
            {"from": "scanner", "to": "specialist", "condition": "always", "label": "Report"},
        ],
    }
    await client.post("/workflows/definitions", json={
        "id": "custom_feedback_test",
        "name": "Custom Feedback",
        "runtime_template": "code_review_loop",
        "definition_json": json.dumps(definition),
    })

    wf_id = "wf_custom_fb"
    await client.post("/workflows/run", json={
        "template_id": "custom_feedback_test",
        "workflow_id": wf_id,
        "user_prompt": "def run(): return eval(input())",
    })
    events = await wait_for_workflow_complete(client)
    assert any(
        e.get("type") == "TOOL_EXECUTION" and e.get("data", {}).get("tool") == "feedback_loop"
        for e in events
    )


@pytest.mark.asyncio
async def test_messages_workflows_lists_completed_run(client, seeded_agents, mock_llm):
    wf_id = "wf_list_workflows"
    await client.post("/workflows/run", json={
        "template_id": "direct_answer",
        "workflow_id": wf_id,
        "user_prompt": "ping",
    })
    await wait_for_workflow_complete(client)

    wf_ids = (await client.get("/messages/workflows")).json()
    assert wf_id in wf_ids


@pytest.mark.asyncio
async def test_multi_turn_conversation_reuses_workflow_memory(
    client, seeded_agents, mock_llm, monkeypatch,
):
    """Same workflow_id across turns: second invoke should see first-turn context."""
    captured_prompts: list[str] = []

    async def capture_invoke(agent_name, model, prompt, workflow_id):
        captured_prompts.append(prompt)
        from app.engine import ws_manager

        await ws_manager.broadcast({
            "type": "AGENT_START",
            "data": {"agent_name": agent_name, "workflow_id": workflow_id},
        })
        await ws_manager.broadcast({
            "type": "TOKEN_STREAM",
            "data": {"token": "ok", "agent_name": agent_name, "metrics": None},
        })
        return f"Reply from {agent_name}"

    monkeypatch.setattr("app.engine.invoke_local_agent", capture_invoke)

    await client.post("/agents", json=agent_payload(
        id="agent_trip_planner",
        name="Trip Planner",
        role="Travel planner",
        skills="trip planning, itineraries, travel",
        system_prompt="You plan trips. Remember destination from prior messages.",
        tools="none",
    ))

    conv_id = "conv_multi_turn_1"
    await client.post("/workflows/run", json={
        "template_id": "direct_answer",
        "workflow_id": conv_id,
        "user_prompt": "Plan a 5-day trip to Tokyo",
    })
    await wait_for_workflow_complete(client)
    captured_prompts.clear()

    await client.post("/workflows/run", json={
        "template_id": "direct_answer",
        "workflow_id": conv_id,
        "user_prompt": "Budget is $2000 and I prefer hotels near Shibuya",
    })
    await wait_for_workflow_complete(client)

    assert captured_prompts, "Expected agent invocation on second turn"
    combined = "\n".join(captured_prompts)
    assert "Tokyo" in combined
    assert "Budget is $2000" in combined or "Shibuya" in combined

    msgs = (await client.get("/messages", params={"workflow_id": conv_id})).json()
    user_msgs = [m for m in msgs if m["sender"] == "Human (Web)"]
    assert len(user_msgs) >= 2
