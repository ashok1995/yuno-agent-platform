"""
Workflow graph definitions for the visual builder and runtime.

Agent nodes use generic slots (orchestrator / specialist) — the registry resolves
which DB agent runs each slot at execution time.
"""
from __future__ import annotations

import json
from typing import Any

BUILTIN_TEMPLATES: dict[str, dict[str, Any]] = {
    "code_review_loop": {
        "id": "code_review_loop",
        "name": "Code Review & Security Audit",
        "description": (
            "Orchestrator plans → security tools → Specialist report (agent chosen from registry). "
            "Feedback loop: if CRITICAL findings, Orchestrator re-plans and Specialist revises."
        ),
        "feedback_loop": True,
        "nodes": [
            {"id": "router", "type": "agent", "label": "Orchestrator", "agent_slot": "orchestrator"},
            {"id": "code_quality", "type": "tool", "label": "code_reviewer", "tool": "code_reviewer"},
            {"id": "scanner", "type": "tool", "label": "security_scanner", "tool": "security_scanner"},
            {"id": "specialist", "type": "agent", "label": "Specialist", "agent_slot": "specialist"},
        ],
        "edges": [
            {"from": "start", "to": "router", "condition": "always", "label": "Begin"},
            {"from": "router", "to": "code_quality", "condition": "always", "label": "Plan → quality scan"},
            {"from": "code_quality", "to": "scanner", "condition": "always", "label": "Quality → security scan"},
            {"from": "scanner", "to": "specialist", "condition": "always", "label": "Findings → report"},
            {
                "from": "specialist",
                "to": "router",
                "condition": "scan_has_critical",
                "label": "Feedback loop (CRITICAL found)",
            },
        ],
    },
    "financial_analysis": {
        "id": "financial_analysis",
        "name": "Financial Analysis",
        "description": (
            "Orchestrator → math tool → Specialist interpretation (finance agent from registry)."
        ),
        "feedback_loop": False,
        "nodes": [
            {"id": "router", "type": "agent", "label": "Orchestrator", "agent_slot": "orchestrator"},
            {"id": "math", "type": "tool", "label": "math_evaluator", "tool": "math_evaluator"},
            {"id": "specialist", "type": "agent", "label": "Specialist", "agent_slot": "specialist"},
        ],
        "edges": [
            {"from": "start", "to": "router", "condition": "intent_is_financial", "label": "Route finance"},
            {"from": "router", "to": "math", "condition": "always", "label": "Extract & compute"},
            {"from": "math", "to": "specialist", "condition": "computation_ok", "label": "Interpret result"},
        ],
    },
    "direct_answer": {
        "id": "direct_answer",
        "name": "Direct Answer",
        "description": (
            "Single specialist node — registry picks the best agent for the prompt "
            "(e.g. trip planner, default reasoning specialist)."
        ),
        "feedback_loop": False,
        "nodes": [
            {"id": "specialist", "type": "agent", "label": "Specialist", "agent_slot": "specialist"},
        ],
        "edges": [
            {"from": "start", "to": "specialist", "condition": "always", "label": "Answer directly"},
        ],
    },
    "dynamic_router_intent": {
        "id": "dynamic_router_intent",
        "name": "Auto-Route (Dynamic)",
        "description": (
            "Orchestrator classifies intent via registry + Qwen, then dispatches to the best template."
        ),
        "feedback_loop": False,
        "nodes": [
            {"id": "router", "type": "agent", "label": "Orchestrator", "agent_slot": "orchestrator"},
            {"id": "template_dispatch", "type": "condition", "label": "Intent classifier"},
            {"id": "code_review_loop", "type": "template", "label": "→ Code Review"},
            {"id": "financial_analysis", "type": "template", "label": "→ Financial"},
            {"id": "direct_answer", "type": "template", "label": "→ Direct Answer"},
        ],
        "edges": [
            {"from": "start", "to": "router", "condition": "always", "label": "Classify"},
            {"from": "router", "to": "template_dispatch", "condition": "always", "label": "Route"},
            {"from": "template_dispatch", "to": "code_review_loop", "condition": "intent_is_code", "label": "Code"},
            {"from": "template_dispatch", "to": "financial_analysis", "condition": "intent_is_financial", "label": "Finance"},
            {"from": "template_dispatch", "to": "direct_answer", "condition": "default", "label": "Default"},
        ],
    },
}

CONDITION_LABELS = {
    "always": "Always",
    "intent_is_financial": "Intent = financial",
    "intent_is_code": "Intent = code / security",
    "computation_ok": "Computation succeeded",
    "scan_has_critical": "Scan has CRITICAL findings",
    "default": "Default / fallback",
}

AGENT_SLOT_LABELS = {
    "orchestrator": "Orchestrator (from registry)",
    "specialist": "Specialist (from registry — resolved per prompt)",
}


def list_all_templates() -> list[dict[str, Any]]:
    return list(BUILTIN_TEMPLATES.values())


def get_template(template_id: str) -> dict[str, Any] | None:
    return BUILTIN_TEMPLATES.get(template_id)


def to_mermaid(definition: dict[str, Any]) -> str:
    """Render workflow graph as Mermaid flowchart for the UI."""
    lines = ["flowchart LR"]
    node_styles: list[str] = []

    for node in definition.get("nodes", []):
        nid = node["id"]
        label = node.get("label", nid)
        ntype = node.get("type", "agent")
        slot = node.get("agent_slot")
        if slot:
            label = f"{label} [{AGENT_SLOT_LABELS.get(slot, slot)}]"
        if ntype == "agent":
            lines.append(f'    {nid}(["{label}"])')
            node_styles.append(f"    style {nid} fill:#1a3a6a,color:#8ab4f8")
        elif ntype == "tool":
            lines.append(f'    {nid}[["{label}"]]')
            node_styles.append(f"    style {nid} fill:#5a4000,color:#fcd080")
        elif ntype == "condition":
            lines.append(f'    {nid}{{{{{label}}}}}')
            node_styles.append(f"    style {nid} fill:#4a1a7a,color:#c0a0f0")
        else:
            lines.append(f'    {nid}["{label}"]')

    lines.append('    start((Start))')
    lines.append('    style start fill:#0a2010,color:#7ae0a0')

    for edge in definition.get("edges", []):
        src = "start" if edge["from"] == "start" else edge["from"]
        dst = edge["to"]
        cond = edge.get("condition", "always")
        elabel = edge.get("label") or CONDITION_LABELS.get(cond, cond)
        lines.append(f'    {src} -->|"{elabel}"| {dst}')

    if definition.get("feedback_loop"):
        lines.append('    specialist -.->|feedback| router')

    lines.extend(node_styles)
    return "\n".join(lines)


def definition_from_json(raw: str) -> dict[str, Any]:
    return json.loads(raw)
