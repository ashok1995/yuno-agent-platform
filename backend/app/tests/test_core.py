"""
Optional smoke tests against a **running** local server (localhost:8000).

Skipped automatically when the backend is not up. Primary coverage lives in
test_critical_paths.py and test_workflow_integration.py (in-process, no server).
"""
import time
import unittest

import httpx
import pytest

BASE_URL = "http://localhost:8000"


def _server_available() -> bool:
    try:
        resp = httpx.get(f"{BASE_URL}/agents", timeout=1.0)
        return resp.status_code == 200
    except Exception:
        return False


@pytest.mark.integration
@unittest.skipUnless(_server_available(), "Backend not running on localhost:8000")
class TestYunoPlatformLiveServer(unittest.TestCase):
    """Smoke tests for manual / CI runs with uvicorn already started."""

    def test_01_agent_registry_returns_seeded_agents(self):
        response = httpx.get(f"{BASE_URL}/agents")
        self.assertEqual(response.status_code, 200)
        agents = response.json()
        if len(agents) < 2:
            self.skipTest(
                "No seeded agents in DB — delete platform.db and restart uvicorn to re-seed."
            )
        self.assertGreaterEqual(len(agents), 2)

    def test_02_agent_extended_crud_lifecycle(self):
        test_payload = {
            "id": "test_qa_agent",
            "name": "Integration Tester",
            "role": "Validation Node",
            "system_prompt": "Assert system metrics behavior.",
            "model": "llama3.1:8b",
            "tools": "security_scanner",
            "channels": "web",
            "schedules": "on_demand",
            "memory_window": 3,
            "skills": "Unit validation testing",
            "interaction_rules": "Execute processes immediately.",
            "guardrails": "Never leak data.",
        }
        upsert_res = httpx.post(f"{BASE_URL}/agents", json=test_payload)
        self.assertEqual(upsert_res.status_code, 200)

        registry = httpx.get(f"{BASE_URL}/agents").json()
        saved_agent = next((a for a in registry if a["id"] == "test_qa_agent"), None)
        self.assertIsNotNone(saved_agent)
        self.assertEqual(saved_agent["memory_window"], 3)

        del_res = httpx.delete(f"{BASE_URL}/agents/test_qa_agent")
        self.assertEqual(del_res.status_code, 200)

    def test_03_workflow_queue_accepts_202(self):
        workflow_payload = {
            "template_id": "code_review_loop",
            "workflow_id": f"test_wf_{int(time.time())}",
            "user_prompt": "def test_func(): password = '123'; eval('run')",
        }
        response = httpx.post(f"{BASE_URL}/workflows/run", json=workflow_payload, timeout=10.0)
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json().get("status"), "queued")
