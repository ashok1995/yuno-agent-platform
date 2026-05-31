import json
import logging

from app.config import settings
from app.agent_registry import resolve_specialist
from app.database import SessionLocal, WorkflowDefinitionModel
from app.engine import ws_manager, call_ollama_collect

logger = logging.getLogger("yuno.runtime")

BUILTIN_TEMPLATES = frozenset({
    "code_review_loop",
    "financial_analysis",
    "direct_answer",
})

SPECIALTY_PROMPT_HINTS = [
    (["invest", "invested", "interest", "loan", "roi", "compound", "mortgage", "annual return", "annual rate", "%"], "financial_analysis"),
    (["trip", "travel", "itinerary", "vacation", "flight", "hotel", "plan a trip", "trip plan"], "direct_answer"),
    (["security", "vulnerability", "audit", "code review", "eval("], "code_review_loop"),
]

TELEGRAM_FRIENDLY_FALLBACK = (
    "Hello! 👋 I'm the Yuno multi-agent assistant.\n\n"
    "I can help with:\n"
    "• Trip planning (e.g. Plan a 3-day trip to Dharamshala)\n"
    "• Finance (e.g. 1000 at 10% for 3 years)\n"
    "• Code review & security\n"
    "• General questions\n\n"
    "What would you like to do?"
)


class OrchestrationRouterService:
    """
    Picks which *workflow template* to run. Specialist agent selection is delegated
    to the agent registry at execution time (generic specialist node).
    """

    @staticmethod
    def _load_custom_workflow_ids() -> list[str]:
        db = SessionLocal()
        try:
            return [row.id for row in db.query(WorkflowDefinitionModel).all()]
        finally:
            db.close()

    @staticmethod
    def _is_small_talk(prompt: str) -> bool:
        """Fast-path greetings — skip slow Ollama JSON routing."""
        t = prompt.lower().strip().rstrip("?!.")
        if not t or len(t.split()) > 8:
            return False
        markers = (
            "hi", "hello", "hey", "howdy", "good morning", "good evening",
            "good afternoon", "how are you", "how are u", "how r u",
            "what's up", "whats up", "sup", "thanks", "thank you", "bye", "goodbye",
        )
        if any(t == m or t.startswith(f"{m} ") or t.startswith(f"{m},") for m in markers):
            return True
        words = t.split()
        return len(words) <= 5 and any(w in ("hi", "hello", "hey", "hiya") for w in words)

    @classmethod
    def _heuristic_template(cls, user_prompt: str) -> str | None:
        prompt_l = user_prompt.lower()
        if cls._is_small_talk(user_prompt):
            return "direct_answer"
        for keywords, template in SPECIALTY_PROMPT_HINTS:
            if any(kw in prompt_l for kw in keywords):
                return template
        return None

    @classmethod
    def _build_routing_prompt(cls, user_prompt: str, custom_ids: list[str]) -> str:
        custom_lines = [f"- {cid} — custom saved workflow" for cid in custom_ids]
        return f"""Classify this input into EXACTLY ONE workflow category. Reply with ONLY JSON, no markdown.

Workflows (specialist agent is chosen automatically from the agent registry):
- code_review_loop: code review, security audit, vulnerability scan
- financial_analysis: compound interest, ROI, loans, investment calculations
- direct_answer: general Q&A, trip planning, knowledge, anything else

Custom workflows:
{chr(10).join(custom_lines) if custom_lines else "- (none)"}

Input: "{user_prompt}"

JSON only: {{"category": "direct_answer"}}"""

    @staticmethod
    async def resolve_topology(
        user_prompt: str,
        requested_template: str,
        *,
        current_prompt: str | None = None,
    ) -> str:
        if requested_template != "dynamic_router_intent":
            return requested_template

        current = (current_prompt or user_prompt).strip()
        custom_ids = OrchestrationRouterService._load_custom_workflow_ids()

        heuristic = OrchestrationRouterService._heuristic_template(current)
        if heuristic:
            await OrchestrationRouterService._broadcast_route(heuristic, user_prompt, current)
            return heuristic

        routing_prompt = OrchestrationRouterService._build_routing_prompt(user_prompt, custom_ids)
        final_topology = "direct_answer"
        valid = set(BUILTIN_TEMPLATES) | set(custom_ids)

        try:
            accumulated_response = await call_ollama_collect(
                settings.ROUTING_MODEL, routing_prompt
            )

            clean_str = accumulated_response.strip()
            if "```json" in clean_str:
                clean_str = clean_str.split("```json")[1].split("```")[0].strip()
            elif "```" in clean_str:
                clean_str = clean_str.split("```")[1].split("```")[0].strip()

            start = clean_str.find("{")
            end = clean_str.rfind("}") + 1
            if start != -1 and end > start:
                clean_str = clean_str[start:end]

            if clean_str:
                routing_result = json.loads(clean_str)
                detected = routing_result.get("category", "direct_answer")
                if detected in valid:
                    final_topology = detected

        except Exception as err:
            logger.error(f"Qwen routing error: {err}. Falling back to direct_answer.")
            final_topology = "direct_answer"

        finally:
            logger.info(f"Workflow routing → [{final_topology}] (model={settings.ROUTING_MODEL})")

        await OrchestrationRouterService._broadcast_route(final_topology, user_prompt, current)
        return final_topology

    @staticmethod
    async def _broadcast_route(
        topology: str,
        user_prompt: str,
        current_prompt: str | None = None,
    ) -> None:
        db = SessionLocal()
        try:
            specialist = resolve_specialist(
                db,
                user_prompt,
                topology,
                current_prompt=current_prompt or user_prompt,
            )
            specialist_hint = (
                f" → registry specialist: {specialist.name} (`{specialist.id}`)"
                if specialist
                else ""
            )
        finally:
            db.close()

        try:
            await ws_manager.broadcast({
                "type": "TOOL_EXECUTION",
                "data": {
                    "tool": "Orchestrator Router",
                    "result": f"Workflow [{topology}]{specialist_hint}",
                },
            })
        except Exception as ws_err:
            logger.error(f"Failed to broadcast routing result: {ws_err}")
