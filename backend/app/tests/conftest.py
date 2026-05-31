"""
Shared pytest fixtures for in-process FastAPI testing (no live server required).

Uses platform_test.db — never wipes production platform.db.
"""
import asyncio
import os
import time
from unittest.mock import patch

# Must set before app.database is imported (selects platform_test.db).
os.environ["YUNO_ENV"] = "test"

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.database import AgentModel, Base, DATABASE_FILE, MessageLogModel, engine
from app.event_store import clear_events
from app.main import app

assert DATABASE_FILE.name == "platform_test.db", (
    f"Tests must not use production DB; got {DATABASE_FILE}"
)


@pytest.fixture(scope="session", autouse=True)
def _disable_telegram_startup():
    """Avoid starting Telegram polling during tests."""
    with patch("app.main.start_telegram_channel_polling"):
        yield


@pytest.fixture(autouse=True)
def fresh_db():
    """Isolated SQLite schema per test (test DB only)."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(autouse=True)
def clear_event_store():
    clear_events()
    yield
    clear_events()


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def agent_payload(**overrides) -> dict:
    base = {
        "id": "test_agent_01",
        "name": "Test Agent",
        "role": "tester",
        "system_prompt": "You are a test agent.",
        "model": "llama3",
        "tools": "none",
        "channels": "web",
        "schedules": "on_demand",
        "memory_window": 10,
        "skills": "testing",
        "interaction_rules": "be brief",
        "guardrails": "no secrets",
    }
    base.update(overrides)
    return base


@pytest_asyncio.fixture
async def seeded_agents(client):
    """Router + specialist + code reviewer agents required by execute_workflow_pipeline."""
    await client.post("/agents", json=agent_payload(
        id="agent_router",
        name="Orchestrator Router",
        role="coordinator",
    ))
    await client.post("/agents", json=agent_payload(
        id="agent_specialist",
        name="Reasoning Specialist",
        role="executor",
    ))
    await client.post("/agents", json=agent_payload(
        id="agent_code_reviewer",
        name="Code Review Specialist",
        role="security analyst",
    ))
    return client


@pytest.fixture
def mock_llm(monkeypatch):
    """Replace Ollama calls with deterministic mock agent responses."""

    async def fake_invoke(agent_name: str, model: str, prompt: str, workflow_id: str) -> str:
        from app.engine import ws_manager

        await ws_manager.broadcast({
            "type": "AGENT_START",
            "data": {"agent_name": agent_name, "workflow_id": workflow_id},
        })
        text = f"Mock output from {agent_name}."
        await ws_manager.broadcast({
            "type": "TOKEN_STREAM",
            "data": {
                "token": text,
                "agent_name": agent_name,
                "metrics": {"total_tokens": 12, "cost": 0.0002},
            },
        })
        return text

    monkeypatch.setattr("app.engine.invoke_local_agent", fake_invoke)
    return fake_invoke


async def wait_for_workflow_complete(client: AsyncClient, timeout: float = 5.0) -> list:
    """Poll /events until WORKFLOW_COMPLETE or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = await client.get("/events")
        events = resp.json().get("events", [])
        if any(e.get("type") == "WORKFLOW_COMPLETE" for e in events):
            return events
        await asyncio.sleep(0.05)
    pytest.fail("Timed out waiting for WORKFLOW_COMPLETE event")
