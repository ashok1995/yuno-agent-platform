"""Tests for workflow template API and graph definitions."""
import json

import pytest

from app.workflow_definitions import (
    BUILTIN_TEMPLATES,
    get_template,
    list_all_templates,
    to_mermaid,
)


def test_builtin_templates_count():
    assert len(list_all_templates()) >= 3


def test_code_review_has_feedback_loop():
    tpl = get_template("code_review_loop")
    assert tpl is not None
    assert tpl["feedback_loop"] is True
    conditions = {e["condition"] for e in tpl["edges"]}
    assert "scan_has_critical" in conditions


def test_mermaid_render_non_empty():
    tpl = get_template("financial_analysis")
    diagram = to_mermaid(tpl)
    assert "flowchart" in diagram
    assert "math" in diagram


@pytest.mark.asyncio
async def test_list_templates_api(client):
    resp = await client.get("/workflows/templates")
    assert resp.status_code == 200
    ids = {t["id"] for t in resp.json()}
    assert "code_review_loop" in ids
    assert "financial_analysis" in ids


@pytest.mark.asyncio
async def test_get_template_detail_with_mermaid(client):
    resp = await client.get("/workflows/templates/direct_answer")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mermaid"]
    assert body["custom"] is False


@pytest.mark.asyncio
async def test_save_custom_workflow_definition(client):
    definition = {
        "id": "custom_test_wf",
        "name": "Test Custom",
        "description": "Custom graph",
        "feedback_loop": False,
        "nodes": [{"id": "specialist", "type": "agent", "label": "Specialist"}],
        "edges": [{"from": "start", "to": "specialist", "condition": "always", "label": "Go"}],
    }
    resp = await client.post("/workflows/definitions", json={
        "id": "custom_test_wf",
        "name": "Test Custom",
        "description": "Custom graph",
        "runtime_template": "direct_answer",
        "definition_json": json.dumps(definition),
    })
    assert resp.status_code == 200

    listed = await client.get("/workflows/templates")
    ids = {t["id"] for t in listed.json()}
    assert "custom_test_wf" in ids

    detail = await client.get("/workflows/templates/custom_test_wf")
    assert detail.status_code == 200
    assert detail.json()["custom"] is True

    deleted = await client.delete("/workflows/definitions/custom_test_wf")
    assert deleted.json()["status"] == "deleted"


@pytest.mark.asyncio
async def test_cannot_delete_builtin_template(client):
    resp = await client.delete("/workflows/definitions/code_review_loop")
    assert resp.status_code == 400
