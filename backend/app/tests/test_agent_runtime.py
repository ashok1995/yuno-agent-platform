"""Tests for agent runtime configuration wiring."""
import json

import pytest

from app.agent_runtime import (
    agent_supports_channel,
    build_agent_prompt,
    build_conversation_context,
    load_agent_memory,
    parse_agent_tools,
)
from app.database import AgentModel, MessageLogModel, SessionLocal
from app.tests.conftest import agent_payload, wait_for_workflow_complete


def _make_agent(**kwargs) -> AgentModel:
    defaults = agent_payload()
    defaults.update(kwargs)
    return AgentModel(**defaults)


def test_parse_agent_tools_ignores_none():
    assert parse_agent_tools("math_solver, none, security_scanner") == [
        "math_solver",
        "security_scanner",
    ]


def test_build_agent_prompt_includes_all_config_fields():
    agent = _make_agent(
        system_prompt="You are a planner.",
        skills="routing, planning",
        interaction_rules="Be concise.",
        guardrails="Never leak secrets.",
    )
    prompt = build_agent_prompt(agent, "Analyze this task.")
    assert "You are a planner." in prompt
    assert "routing, planning" in prompt
    assert "Be concise." in prompt
    assert "Never leak secrets." in prompt
    assert "Analyze this task." in prompt


def test_build_agent_prompt_includes_memory():
    agent = _make_agent(memory_window=5)
    memory = [
        MessageLogModel(workflow_id="wf1", sender="Human (Web)", content="Hello"),
        MessageLogModel(workflow_id="wf1", sender="Router", content="Planning..."),
    ]
    prompt = build_agent_prompt(agent, "Continue.", memory=memory)
    assert "Human (Web): Hello" in prompt
    assert "Router: Planning..." in prompt


def test_load_agent_memory_excludes_duplicate_current_user():
    wf_id = "conv_memory_test"
    db = SessionLocal()
    try:
        db.add_all([
            MessageLogModel(workflow_id=wf_id, sender="Human (Web)", content="Plan trip to Tokyo"),
            MessageLogModel(workflow_id=wf_id, sender="Trip Planner", content="What is your budget?"),
            MessageLogModel(workflow_id=wf_id, sender="Human (Web)", content="Budget is $2000"),
        ])
        db.commit()

        memory = load_agent_memory(
            db, wf_id, 5, exclude_latest_user="Budget is $2000",
        )
        senders = [m.sender for m in memory]
        assert "Human (Web)" in senders
        assert memory[-1].content == "What is your budget?"
        assert not any(m.content == "Budget is $2000" for m in memory)
    finally:
        db.close()


def test_build_conversation_context_includes_prior_turns():
    wf_id = "conv_context_test"
    db = SessionLocal()
    try:
        db.add_all([
            MessageLogModel(workflow_id=wf_id, sender="Human (Web)", content="Plan a trip to Tokyo"),
            MessageLogModel(workflow_id=wf_id, sender="Trip Planner", content="When do you want to travel?"),
        ])
        db.commit()

        ctx = build_conversation_context(db, wf_id, "Next weekend, 5 days")
        assert "Plan a trip to Tokyo" in ctx
        assert "Next weekend, 5 days" in ctx
    finally:
        db.close()


def test_agent_supports_channel():
    agent = _make_agent(channels="web,telegram")
    assert agent_supports_channel(agent, "web")
    assert agent_supports_channel(agent, "telegram")
    assert not agent_supports_channel(agent, "slack")


@pytest.mark.asyncio
async def test_custom_agent_system_prompt_used_at_runtime(client, seeded_agents, monkeypatch):
    captured: list[str] = []

    async def capture_invoke(agent_name, model, prompt, workflow_id):
        captured.append(prompt)
        from app.engine import ws_manager

        await ws_manager.broadcast({
            "type": "AGENT_START",
            "data": {"agent_name": agent_name, "workflow_id": workflow_id},
        })
        await ws_manager.broadcast({
            "type": "TOKEN_STREAM",
            "data": {"token": "ok", "agent_name": agent_name, "metrics": None},
        })
        return "Custom agent response."

    monkeypatch.setattr("app.engine.invoke_local_agent", capture_invoke)

    marker = "UNIQUE_SYSTEM_MARKER_XYZ"
    await client.post("/agents", json=agent_payload(
        id="agent_custom",
        name="Custom Analyst",
        system_prompt=marker,
        tools="none",
        channels="web",
    ))

    definition = {
        "id": "wf_custom_agent",
        "nodes": [
            {
                "id": "specialist",
                "type": "agent",
                "label": "Custom Analyst",
                "agent_id": "agent_custom",
            },
        ],
        "edges": [{"from": "start", "to": "specialist", "condition": "always"}],
    }
    await client.post("/workflows/definitions", json={
        "id": "wf_custom_agent",
        "name": "Custom Agent Workflow",
        "runtime_template": "direct_answer",
        "definition_json": json.dumps(definition),
    })

    await client.post("/workflows/run", json={
        "template_id": "wf_custom_agent",
        "workflow_id": "run_custom_1",
        "user_prompt": "hello custom agent",
    })
    await wait_for_workflow_complete(client)

    assert captured, "Expected at least one Ollama invocation"
    assert any(marker in p for p in captured)


@pytest.mark.asyncio
async def test_agent_channel_filter_skips_web_only_agent_on_telegram(
    client, seeded_agents, monkeypatch,
):
    invoke_count = {"n": 0}

    async def counting_invoke(agent_name, model, prompt, workflow_id):
        invoke_count["n"] += 1
        from app.engine import ws_manager

        await ws_manager.broadcast({
            "type": "WORKFLOW_COMPLETE",
            "data": {"status": "completed"},
        })
        return "done"

    monkeypatch.setattr("app.engine.invoke_local_agent", counting_invoke)

    await client.post("/agents", json=agent_payload(
        id="agent_web_only",
        name="Web Only Bot",
        channels="web",
        tools="none",
    ))

    definition = {
        "id": "wf_web_only",
        "nodes": [
            {"id": "a1", "type": "agent", "label": "Web Only", "agent_id": "agent_web_only"},
        ],
        "edges": [{"from": "start", "to": "a1", "condition": "always"}],
    }
    await client.post("/workflows/definitions", json={
        "id": "wf_web_only",
        "name": "Web Only",
        "runtime_template": "direct_answer",
        "definition_json": json.dumps(definition),
    })

    from app.graph import execute_workflow_pipeline

    await execute_workflow_pipeline("wf_web_only", "tg_test", "hi", channel="telegram")
    assert invoke_count["n"] == 0

    await execute_workflow_pipeline("wf_web_only", "web_test", "hi", channel="web")
    assert invoke_count["n"] == 1
