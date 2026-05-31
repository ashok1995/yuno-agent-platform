"""
Agent runtime — applies DB agent configuration at execution time.

Uses system_prompt, skills, interaction_rules, guardrails, memory_window,
tools, channels, and schedules from AgentModel records.
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from app.engine import ws_manager
from app.tools import route_and_execute_tool

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.database import AgentModel, MessageLogModel

logger = logging.getLogger("yuno.agent_runtime")


def parse_agent_tools(tools_field: str | None) -> list[str]:
    """Parse comma-separated tool names; ignore empty / 'none'."""
    if not tools_field:
        return []
    return [
        t.strip()
        for t in tools_field.split(",")
        if t.strip() and t.strip().lower() not in ("none", "null")
    ]


def agent_supports_channel(agent: AgentModel, channel: str) -> bool:
    channels = [c.strip().lower() for c in (agent.channels or "web").split(",")]
    return channel.lower() in channels or "all" in channels


def agent_available_for_schedule(agent: AgentModel) -> bool:
    """
    on_demand / continuous: eligible for workflow triggers.
    Other schedule strings are stored for future cron — allow manual runs.
    """
    schedule = (agent.schedules or "on_demand").strip().lower()
    return schedule in ("on_demand", "continuous", "manual", "")


USER_SENDERS = frozenset({"Human (Web)", "Human (Telegram)"})


def conversation_message_limit(memory_window: int) -> int:
    """Load enough rows to cover multi-agent turns (orchestrator + specialist per user message)."""
    base = memory_window if memory_window > 0 else 5
    return min(50, max(base, 5) * 4)


def load_agent_memory(
    db: Session,
    workflow_id: str,
    memory_window: int,
    *,
    exclude_latest_user: str | None = None,
) -> list[MessageLogModel]:
    """Recent messages for this conversation thread (shared workflow_id across turns)."""
    if memory_window <= 0:
        return []
    from app.database import MessageLogModel

    rows = (
        db.query(MessageLogModel)
        .filter(MessageLogModel.workflow_id == workflow_id)
        .order_by(MessageLogModel.id.desc())
        .limit(conversation_message_limit(memory_window))
        .all()
    )
    rows = list(reversed(rows))
    if exclude_latest_user and rows:
        last = rows[-1]
        if (
            last.sender in USER_SENDERS
            and last.content.strip() == exclude_latest_user.strip()
        ):
            rows = rows[:-1]
    return rows


def build_conversation_context(
    db: Session,
    workflow_id: str,
    current_prompt: str,
    *,
    memory_limit: int = 20,
) -> str:
    """Merge prior turns with the current message for routing and agent selection."""
    prior = load_agent_memory(db, workflow_id, memory_limit)
    if not prior:
        return current_prompt.strip()
    lines = [f"{m.sender}: {m.content[:600]}" for m in prior]
    return (
        f"Current user message:\n{current_prompt.strip()}\n\n"
        "Prior conversation (same session):\n"
        + "\n".join(lines)
    )


def build_agent_prompt(
    agent: AgentModel,
    task: str,
    memory: list[MessageLogModel] | None = None,
    extra_context: str | None = None,
) -> str:
    """Compose full Ollama prompt from agent profile + optional memory + task."""
    sections: list[str] = []

    if agent.system_prompt:
        sections.append(f"=== System ===\n{agent.system_prompt.strip()}")

    if agent.role:
        sections.append(f"=== Role ===\n{agent.role.strip()}")

    if agent.skills:
        sections.append(f"=== Skills ===\n{agent.skills.strip()}")

    if agent.interaction_rules:
        sections.append(f"=== Interaction rules ===\n{agent.interaction_rules.strip()}")

    if agent.guardrails:
        sections.append(f"=== Guardrails (must follow) ===\n{agent.guardrails.strip()}")

    if memory:
        lines = [f"{m.sender}: {m.content[:800]}" for m in memory]
        sections.append("=== Recent conversation (this session) ===\n" + "\n".join(lines))

    if extra_context:
        sections.append(f"=== Context ===\n{extra_context.strip()}")

    sections.append(f"=== Task ===\n{task.strip()}")
    return "\n\n".join(sections)


async def run_tool(tool_name: str, prompt: str) -> str:
    await ws_manager.broadcast({"type": "TOOL_START", "data": {"tool": tool_name}})
    result = route_and_execute_tool(tool_name, prompt)
    await ws_manager.broadcast({
        "type": "TOOL_EXECUTION",
        "data": {"tool": tool_name, "result": result},
    })
    return result


async def run_agent_tools(agent: AgentModel, prompt: str) -> dict[str, str]:
    """Execute all tools listed on the agent profile."""
    results: dict[str, str] = {}
    for tool_name in parse_agent_tools(agent.tools):
        results[tool_name] = await run_tool(tool_name, prompt)
    return results


async def invoke_configured_agent(
    db: Session,
    agent: AgentModel,
    task: str,
    workflow_id: str,
    *,
    channel: str = "web",
    extra_context: str | None = None,
    skip_memory: bool = False,
) -> str | None:
    """
    Invoke agent using DB config. Returns None if channel/schedule blocks execution.
    """
    if not agent_supports_channel(agent, channel):
        logger.info(f"Agent {agent.id} skipped — channel '{channel}' not in {agent.channels}")
        await ws_manager.broadcast({
            "type": "TOOL_EXECUTION",
            "data": {
                "tool": "agent_channel_filter",
                "result": f"Skipped {agent.name}: not enabled for channel '{channel}'.",
            },
        })
        return None

    if not agent_available_for_schedule(agent):
        logger.info(f"Agent {agent.id} skipped — schedule '{agent.schedules}'")
        return None

    memory = (
        []
        if skip_memory
        else load_agent_memory(
            db,
            workflow_id,
            agent.memory_window or 5,
            exclude_latest_user=task,
        )
    )
    full_prompt = build_agent_prompt(agent, task, memory=memory, extra_context=extra_context)
    from app.engine import invoke_local_agent

    return await invoke_local_agent(agent.name, agent.model, full_prompt, workflow_id)


def get_agent_by_id(db: Session, agent_id: str) -> AgentModel | None:
    from app.database import AgentModel

    return db.query(AgentModel).filter(AgentModel.id == agent_id).first()


def resolve_workflow_definition(template_id: str, db: Session) -> dict | None:
    """Built-in template or user-saved custom graph from DB."""
    from app.database import WorkflowDefinitionModel
    from app.workflow_definitions import get_template

    builtin = get_template(template_id)
    if builtin:
        return builtin

    custom = (
        db.query(WorkflowDefinitionModel)
        .filter(WorkflowDefinitionModel.id == template_id)
        .first()
    )
    if custom and custom.definition_json:
        try:
            definition = json.loads(custom.definition_json)
            definition.setdefault("id", custom.id)
            definition.setdefault("runtime_template", custom.runtime_template)
            return definition
        except json.JSONDecodeError:
            logger.error(f"Invalid definition_json for workflow {template_id}")
    return None
