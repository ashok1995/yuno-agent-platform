"""
Agent registry — single source of truth for resolving which DB agent runs a graph slot.

Workflow graphs use generic nodes (orchestrator / specialist), not hardcoded agent IDs.
The orchestrator router picks a *workflow*; the registry picks which registry agent
fills each slot using prompt + runtime context + agent profiles from SQLite.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.database import AgentModel

logger = logging.getLogger("yuno.agent_registry")

ORCHESTRATOR_SLOT = "orchestrator"
SPECIALIST_SLOT = "specialist"
DEFAULT_ORCHESTRATOR_ID = "agent_router"
DEFAULT_SPECIALIST_ID = "agent_specialist"

# Runtime workflow → preferred specialist profile keywords (registry scan)
RUNTIME_SPECIALIST_HINTS: dict[str, list[str]] = {
    "code_review_loop": ["code review", "security", "reviewer", "audit"],
    "financial_analysis": ["finance", "financial", "investment"],
    "direct_answer": [],
}

# Prompt keywords → agent profile keywords (for open-ended specialist routing)
SPECIALTY_ROUTES = [
    {
        "prompt_keywords": [
            "invest", "invested", "interest", "loan", "roi", "compound", "mortgage",
            "portfolio", "annual return", "annual rate", "future value", "principal",
            "rupee", "rupees", " rs ", "₹", "percent", "%",
        ],
        "agent_keywords": ["finance", "financial", "investment"],
    },
    {
        "prompt_keywords": [
            "trip", "travel", "itinerary", "vacation", "flight", "hotel",
            "destination", "plan a trip", "road trip", "tourism", "visit",
            "weekend in", "plan a weekend", "holiday", "paragliding", "dharamshala",
        ],
        "agent_keywords": ["trip", "travel", "planner", "itinerary", "tourism", "vacation"],
    },
    {
        "prompt_keywords": [
            "security", "vulnerability", "audit", "code review", "eval(", "sql injection",
        ],
        "agent_keywords": ["security", "code review", "reviewer", "audit"],
    },
]


def profile_text(agent: AgentModel) -> str:
    return " ".join(
        filter(None, [agent.id, agent.name, agent.role, agent.skills or ""])
    ).lower()


def list_agents(db: Session) -> list[AgentModel]:
    from app.database import AgentModel

    return db.query(AgentModel).all()


def get_orchestrator(db: Session) -> AgentModel | None:
    from app.agent_runtime import get_agent_by_id

    agent = get_agent_by_id(db, DEFAULT_ORCHESTRATOR_ID)
    if agent:
        return agent
    for a in list_agents(db):
        if "coordinator" in (a.role or "").lower() or "router" in (a.name or "").lower():
            return a
    return None


def get_default_specialist(db: Session) -> AgentModel | None:
    from app.agent_runtime import get_agent_by_id

    return get_agent_by_id(db, DEFAULT_SPECIALIST_ID) or _first_non_orchestrator(db)


def _first_non_orchestrator(db: Session) -> AgentModel | None:
    for a in list_agents(db):
        if a.id != DEFAULT_ORCHESTRATOR_ID:
            return a
    return None


def _prefer_specialty_agent(candidates: list[AgentModel]) -> AgentModel | None:
    """Prefer custom/domain agents over generic seeded specialist."""
    for agent in candidates:
        if agent.id not in (DEFAULT_ORCHESTRATOR_ID, DEFAULT_SPECIALIST_ID):
            return agent
    return candidates[0] if candidates else None


def _score_specialty_routes(prompt: str) -> list[tuple[int, dict]]:
    """Return routes matched in prompt, highest keyword hit count first."""
    prompt_l = f" {prompt.lower()} "
    scored: list[tuple[int, dict]] = []
    for route in SPECIALTY_ROUTES:
        hits = sum(1 for kw in route["prompt_keywords"] if kw in prompt_l or kw.strip() in prompt_l)
        if hits:
            scored.append((hits, route))
    scored.sort(key=lambda x: -x[0])
    return scored


def _match_route_to_agent(route: dict, agents: list[AgentModel]) -> AgentModel | None:
    matches = []
    for agent in agents:
        if agent.id == DEFAULT_ORCHESTRATOR_ID:
            continue
        if any(kw in profile_text(agent) for kw in route["agent_keywords"]):
            matches.append(agent)
    return _prefer_specialty_agent(matches)


def _is_follow_up_message(text: str) -> bool:
    """
    Short continuation of the prior topic — safe to use conversation context for routing.
    A message with its own clear topic (e.g. finance after travel chat) is NOT a follow-up.
    """
    t = text.strip().lower()
    if len(t) > 150:
        return False
    if _score_specialty_routes(t):
        return False
    markers = (
        "also", "as well", "too", "what about", "instead", "prefer",
        "budget", "how about", "can i", "could i", "please add", "and ",
    )
    return any(m in t for m in markers) or len(t.split()) <= 12


def _match_by_specialty_prompt(user_prompt: str, agents: list[AgentModel]) -> AgentModel | None:
    scored = _score_specialty_routes(user_prompt)
    if not scored:
        return None
    _, route = scored[0]
    return _match_route_to_agent(route, agents)


def _match_by_runtime_hints(runtime_kind: str, agents: list[AgentModel]) -> AgentModel | None:
    hints = RUNTIME_SPECIALIST_HINTS.get(runtime_kind, [])
    if not hints:
        return None
    matches = []
    for agent in agents:
        if agent.id == DEFAULT_ORCHESTRATOR_ID:
            continue
        blob = profile_text(agent)
        if any(h in blob for h in hints):
            matches.append(agent)
    return _prefer_specialty_agent(matches)


def resolve_specialist(
    db: Session,
    user_prompt: str,
    runtime_kind: str = "direct_answer",
    *,
    current_prompt: str | None = None,
) -> AgentModel | None:
    """
    Pick the best specialist agent from the registry for this prompt + workflow type.

    Routing uses the **current user message first** so a new topic in the same
    conversation (e.g. finance after trip planning) does not inherit the old agent.
    Prior turns are only used for short follow-ups like "budget is $2000".
    """
    agents = list_agents(db)
    if not agents:
        return None

    current = (current_prompt or user_prompt).strip()

    # Workflow-specific preference (code review → code reviewer, etc.)
    by_runtime = _match_by_runtime_hints(runtime_kind, agents)
    if by_runtime and runtime_kind != "direct_answer":
        logger.info(f"Registry specialist (runtime={runtime_kind}) → {by_runtime.id}")
        return by_runtime

    # 1) Current turn — primary signal
    by_current = _match_by_specialty_prompt(current, agents)
    if by_current:
        logger.info(f"Registry specialist (current turn) → {by_current.id}")
        return by_current

    # 2) Follow-up clarifications — use full conversation context
    if current_prompt and user_prompt.strip() != current and _is_follow_up_message(current):
        by_context = _match_by_specialty_prompt(user_prompt, agents)
        if by_context:
            logger.info(f"Registry specialist (conversation context) → {by_context.id}")
            return by_context

    if by_runtime:
        logger.info(f"Registry specialist (runtime={runtime_kind}) → {by_runtime.id}")
        return by_runtime

    fallback = get_default_specialist(db)
    if fallback:
        logger.info(f"Registry specialist (default) → {fallback.id}")
    return fallback


def resolve_node_agent(
    db: Session,
    node: dict,
    *,
    user_prompt: str,
    runtime_kind: str,
    state: dict,
    current_prompt: str | None = None,
) -> AgentModel | None:
    """
    Resolve a graph agent node to a concrete registry entry.

    Priority: explicit agent_id (custom override) → agent_slot → None
    """
    from app.agent_runtime import get_agent_by_id

    if node.get("agent_id"):
        return get_agent_by_id(db, node["agent_id"])

    slot = node.get("agent_slot") or _legacy_slot_from_node_id(node.get("id", ""))

    if slot == ORCHESTRATOR_SLOT:
        return get_orchestrator(db)

    if slot == SPECIALIST_SLOT:
        cached = state.get("routed_specialist_id")
        if cached:
            agent = get_agent_by_id(db, cached)
            if agent:
                return agent
        agent = resolve_specialist(db, user_prompt, runtime_kind, current_prompt=current_prompt)
        if agent:
            state["routed_specialist_id"] = agent.id
            state["routed_specialist_name"] = agent.name
        return agent

    return None


def _legacy_slot_from_node_id(node_id: str) -> str | None:
    """Backward compat: old graphs used node id 'router' / 'specialist' without agent_slot."""
    if node_id in ("router", "orchestrator"):
        return ORCHESTRATOR_SLOT
    if node_id in ("specialist", "reviewer"):
        return SPECIALIST_SLOT
    return None
