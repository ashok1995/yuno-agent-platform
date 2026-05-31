"""
Execute workflow graphs using agent_id references and agent DB configuration.
"""
from __future__ import annotations

import logging
from typing import Any

from app.agent_registry import ORCHESTRATOR_SLOT, SPECIALIST_SLOT, resolve_node_agent
from app.agent_runtime import (
    agent_supports_channel,
    invoke_configured_agent,
    run_agent_tools,
    run_tool,
)
from app.database import AgentModel, MessageLogModel
from app.engine import ws_manager
from app.financial_parser import try_parse_and_compute

logger = logging.getLogger("yuno.workflow_executor")


def _tool_result_usable(result: str) -> bool:
    skip_prefixes = ("Error:", "No solvable", "No matching knowledge")
    return not any(result.startswith(p) for p in skip_prefixes)


def _condition_met(condition: str, state: dict[str, Any]) -> bool:
    """Evaluate whether a forward edge should be taken."""
    cond = (condition or "always").strip()
    if cond in ("always", "default"):
        return True
    if cond == "computation_ok":
        return state.get("flags", {}).get("computation_ok", True)
    if cond == "scan_has_critical":
        return False  # back-edges handled by explicit feedback_loop pass
    if cond in ("intent_is_financial", "intent_is_code"):
        return True  # intent resolved before subgraph execution
    return True


def _forward_node_order(definition: dict[str, Any], state: dict[str, Any]) -> list[dict[str, Any]]:
    """Walk forward through the graph; skip feedback back-edges."""
    nodes_by_id = {n["id"]: n for n in definition.get("nodes", [])}
    edges = definition.get("edges", [])
    order: list[dict[str, Any]] = []
    visited: set[str] = set()
    current = "start"

    for _ in range(32):
        outgoing = [e for e in edges if e.get("from") == current]
        next_edge = None
        for edge in outgoing:
            cond = edge.get("condition", "always")
            if cond == "scan_has_critical":
                continue
            if _condition_met(cond, state):
                next_edge = edge
                break
        if not next_edge:
            break
        nid = next_edge["to"]
        if nid in visited:
            break
        visited.add(nid)
        node = nodes_by_id.get(nid)
        if node and node.get("type") in ("agent", "tool"):
            order.append(node)
        current = nid

    return order


def _runtime_kind(definition: dict[str, Any]) -> str:
    """Template id used for task/tool specialization (builtin or runtime_template hint)."""
    hint = definition.get("runtime_template") or definition.get("id", "direct_answer")
    if hint in ("code_review_loop", "financial_analysis", "direct_answer"):
        return hint
    return "direct_answer"


def _format_tool_context(tool_outputs: dict[str, str]) -> str:
    if not tool_outputs:
        return ""
    parts = [f"**{name}**:\n{result}" for name, result in tool_outputs.items()]
    return "Tool results:\n\n" + "\n\n".join(parts)


def _agent_task_for_template(
    template_id: str,
    node: dict[str, Any],
    prompt: str,
    state: dict[str, Any],
) -> tuple[str, str | None]:
    """Return (task, extra_context) for an agent node."""
    node_id = node["id"]
    tool_ctx = _format_tool_context(state.get("tool_outputs", {}))

    if template_id == "code_review_loop":
        if node_id == "router":
            task = (
                "Plan the security review milestones and checks needed for the code below. "
                "Be concise and structured.\n\n"
                f"Code:\n{prompt}"
            )
            return task, tool_ctx or None
        if node_id in ("specialist", "reviewer"):
            plan = state.get("agent_outputs", {}).get("router", "")
            task = (
                "Write a final security and code quality report using the tool findings below.\n\n"
                f"Original code:\n{prompt}\n\n"
                f"Review plan:\n{plan}\n\n"
                "Include vulnerabilities, severity, and remediation steps."
            )
            return task, tool_ctx or None

    if template_id == "financial_analysis" and node_id == "specialist":
        computation = state.get("computation", "No computation available.")
        task = (
            "Answer the user's financial question using ONLY the verified computation below. "
            "Never invent dollar amounts.\n\n"
            f"User question: {prompt}\n\n"
            f"Verified computation:\n{computation}\n\n"
            "Respond with: **Final answer**, **Calculation** (one sentence), **Risk notes** (3 bullets)."
        )
        return task, None

    if node_id == "router":
        task = f"Analyze and plan how to handle this request:\n\n{prompt}"
        return task, tool_ctx or None

    task = f"Answer clearly, accurately, and concisely:\n\n{prompt}"
    return task, tool_ctx or None


async def _execute_tool_node(
    template_id: str,
    node: dict[str, Any],
    prompt: str,
    state: dict[str, Any],
    agent_for_tools: AgentModel | None,
) -> str:
    tool_name = node.get("tool", "")

    if template_id == "financial_analysis" and tool_name in ("math_evaluator", "math_solver"):
        computation = try_parse_and_compute(prompt)
        if computation:
            await ws_manager.broadcast({"type": "TOOL_START", "data": {"tool": "math_solver"}})
            await ws_manager.broadcast({
                "type": "TOOL_EXECUTION",
                "data": {"tool": "math_solver", "result": computation},
            })
            state["computation"] = computation
            state["flags"]["computation_ok"] = True
            return computation

        solver = await run_tool("math_solver", prompt)
        if _tool_result_usable(solver):
            state["computation"] = solver
            state["flags"]["computation_ok"] = True
            return solver

        state["computation"] = "Could not parse principal, rate, and term from the prompt."
        state["flags"]["computation_ok"] = False
        return state["computation"]

    if tool_name:
        result = await run_tool(tool_name, prompt)
        state["tool_outputs"][tool_name] = result
        if "CRITICAL" in result:
            state["flags"]["scan_has_critical"] = True
        return result

    if agent_for_tools:
        results = await run_agent_tools(agent_for_tools, prompt)
        state["tool_outputs"].update(results)
        combined = "\n".join(f"{k}: {v}" for k, v in results.items())
        if any("CRITICAL" in v for v in results.values()):
            state["flags"]["scan_has_critical"] = True
        return combined

    return ""


async def _persist_agent_message(db, workflow_id: str, agent: AgentModel, content: str) -> None:
    if content is None:
        return
    db.add(MessageLogModel(workflow_id=workflow_id, sender=agent.name, content=content))
    db.commit()


def _is_orchestrator_node(node: dict[str, Any], agent: AgentModel) -> bool:
    """Orchestrator nodes classify/route only — they must not generate user-facing answers."""
    return (
        node.get("agent_slot") == ORCHESTRATOR_SLOT
        or node.get("id") == "router"
        or agent.id == "agent_router"
    )


async def _broadcast_orchestrator_routing(
    runtime_kind: str,
    *,
    specialist_name: str | None = None,
    feedback: bool = False,
) -> str:
    """Emit routing decision as a tool event — no LLM call, no streamed tokens."""
    if feedback:
        note = (
            f"Re-routing [{runtime_kind}] after CRITICAL findings "
            f"→ handoff to {specialist_name or 'specialist'} for revision."
        )
    else:
        specialist_hint = f" → {specialist_name}" if specialist_name else ""
        note = f"Routing decision: [{runtime_kind}]{specialist_hint} — dispatching to workflow nodes."

    await ws_manager.broadcast({
        "type": "TOOL_EXECUTION",
        "data": {"tool": "Orchestrator Router", "result": note},
    })
    return note


async def execute_workflow_from_definition(
    definition: dict[str, Any],
    db,
    workflow_id: str,
    prompt: str,
    channel: str = "web",
) -> None:
    runtime_kind = _runtime_kind(definition)
    state: dict[str, Any] = {
        "tool_outputs": {},
        "agent_outputs": {},
        "flags": {"computation_ok": True, "scan_has_critical": False},
        "computation": None,
        "routed_specialist_id": None,
        "routed_specialist_name": None,
    }

    # Pre-resolve specialist from registry (visible in routing event + first specialist node)
    from app.agent_registry import resolve_specialist
    from app.agent_runtime import build_conversation_context

    routing_context = build_conversation_context(db, workflow_id, prompt)
    specialist = resolve_specialist(
        db, routing_context, runtime_kind, current_prompt=prompt,
    )
    if specialist:
        state["routed_specialist_id"] = specialist.id
        state["routed_specialist_name"] = specialist.name
        await ws_manager.broadcast({
            "type": "TOOL_EXECUTION",
            "data": {
                "tool": "Agent Registry",
                "result": (
                    f"Specialist slot → **{specialist.name}** (`{specialist.id}`) "
                    f"— instructions loaded from DB profile."
                ),
            },
        })

    node_order = _forward_node_order(definition, state)
    last_agent: AgentModel | None = None

    for node in node_order:
        if node["type"] == "tool":
            agent_for_tools = last_agent
            if not agent_for_tools and state.get("routed_specialist_id"):
                agent_for_tools = resolve_node_agent(
                    db, {"agent_slot": SPECIALIST_SLOT},
                    user_prompt=routing_context,
                    runtime_kind=runtime_kind,
                    state=state,
                    current_prompt=prompt,
                )
            await _execute_tool_node(runtime_kind, node, prompt, state, agent_for_tools)

        elif node["type"] == "agent":
            agent = resolve_node_agent(
                db, node,
                user_prompt=routing_context,
                runtime_kind=runtime_kind,
                state=state,
                current_prompt=prompt,
            )
            if not agent:
                logger.warning(f"Could not resolve agent for node {node.get('id')}")
                continue

            if not agent_supports_channel(agent, channel):
                continue

            if _is_orchestrator_node(node, agent):
                routing_note = await _broadcast_orchestrator_routing(
                    runtime_kind,
                    specialist_name=state.get("routed_specialist_name"),
                )
                state["agent_outputs"][node["id"]] = routing_note
                last_agent = agent
                continue

            task, extra_context = _agent_task_for_template(runtime_kind, node, prompt, state)
            output = await invoke_configured_agent(
                db,
                agent,
                task,
                workflow_id,
                channel=channel,
                extra_context=extra_context,
            )
            if output is not None:
                state["agent_outputs"][node["id"]] = output
                await _persist_agent_message(db, workflow_id, agent, output)
                last_agent = agent

    if state["flags"].get("scan_has_critical") and definition.get("feedback_loop"):
        reviewer = resolve_node_agent(
            db, {"agent_slot": SPECIALIST_SLOT},
            user_prompt=routing_context,
            runtime_kind=runtime_kind,
            state=state,
            current_prompt=prompt,
        )
        reviewer_id = reviewer.id if reviewer else state.get("routed_specialist_id", "")
        if reviewer_id:
            await _run_feedback_loop(db, workflow_id, prompt, state, reviewer_id, channel)


async def _run_feedback_loop(
    db,
    workflow_id: str,
    prompt: str,
    state: dict[str, Any],
    reviewer_id: str,
    channel: str,
) -> None:
    from app.agent_runtime import get_agent_by_id

    reviewer = get_agent_by_id(db, reviewer_id)
    if not reviewer:
        return

    plan = state.get("agent_outputs", {}).get("router", "")

    await ws_manager.broadcast({
        "type": "TOOL_EXECUTION",
        "data": {
            "tool": "feedback_loop",
            "result": "Condition [scan_has_critical] met — looping Router → Specialist.",
        },
    })

    await _broadcast_orchestrator_routing(
        "code_review_loop",
        specialist_name=reviewer.name,
        feedback=True,
    )

    tool_ctx = _format_tool_context(state.get("tool_outputs", {}))
    rev_task2 = (
        "Revise the security report using the updated plan and findings.\n\n"
        f"Updated plan:\n{plan}\n\nOriginal code:\n{prompt}"
    )
    out2 = await invoke_configured_agent(
        db, reviewer, rev_task2, workflow_id, channel=channel, extra_context=tool_ctx,
    )
    if out2:
        await _persist_agent_message(db, workflow_id, reviewer, out2)
