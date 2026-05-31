import datetime
import json
import httpx
import logging
from pathlib import Path

from sqlalchemy import create_engine, Column, String, Text, Integer, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import settings

logger = logging.getLogger("yuno.database")

BACKEND_ROOT = Path(__file__).resolve().parent.parent


def resolve_database_path() -> Path:
    """Always use the same SQLite file regardless of process working directory."""
    import os

    if settings.DATABASE_PATH:
        return Path(settings.DATABASE_PATH).expanduser().resolve()
    if os.environ.get("YUNO_ENV") == "test":
        return BACKEND_ROOT / "platform_test.db"
    return BACKEND_ROOT / "platform.db"


DATABASE_FILE = resolve_database_path()
DATABASE_FILE.parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    f"sqlite:///{DATABASE_FILE}",
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class AgentModel(Base):
    __tablename__ = "agents"
    id                = Column(String,  primary_key=True)
    name              = Column(String,  nullable=False)
    role              = Column(String,  nullable=False)
    system_prompt     = Column(Text,    nullable=False)
    model             = Column(String,  default="qwen3.5:9b")
    tools             = Column(String,  default="math_evaluator,security_scanner")
    channels          = Column(String,  default="web,telegram")
    schedules         = Column(String,  default="on_demand")
    memory_window     = Column(Integer, default=5)
    skills            = Column(Text,    default="Code analysis, structural auditing")
    interaction_rules = Column(Text,    default="Be concise. Address feedback iteratively.")
    guardrails        = Column(Text,    default="Do not reveal internal system credentials.")
    created_at        = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at        = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


class MessageLogModel(Base):
    __tablename__ = "message_logs"
    id          = Column(Integer, primary_key=True, autoincrement=True)
    workflow_id = Column(String, index=True)
    sender      = Column(String, nullable=False)
    content     = Column(Text,   nullable=False)
    timestamp   = Column(DateTime, default=datetime.datetime.utcnow)


class WorkflowDefinitionModel(Base):
    """User-saved workflow graphs from the visual builder."""
    __tablename__ = "workflow_definitions"
    id              = Column(String, primary_key=True)
    name            = Column(String, nullable=False)
    description     = Column(Text, default="")
    runtime_template = Column(String, nullable=False)  # graph.py branch to execute
    definition_json = Column(Text, nullable=False)
    created_at      = Column(DateTime, default=datetime.datetime.utcnow)


class PlatformMetaModel(Base):
    """Key-value store for platform-level flags (e.g. user-deleted demo agents)."""
    __tablename__ = "platform_meta"
    key = Column(String, primary_key=True)
    value = Column(Text, default="")


Base.metadata.create_all(bind=engine)

# Required for runtime — re-created on startup if missing (delete blocked in API).
CORE_DEFAULT_AGENT_IDS = frozenset({
    "agent_router",
    "agent_specialist",
    "agent_code_reviewer",
})

# Seeded on fresh DB; user may delete — never re-added after explicit delete.
OPTIONAL_DEFAULT_AGENT_IDS = frozenset({
    "agent_trip_planner",
})

DELETED_DEFAULT_AGENTS_META_KEY = "deleted_default_agents"


def _migrate_agent_timestamps() -> None:
    """Add timestamp columns to existing SQLite DBs created before this version."""
    import sqlite3

    conn = sqlite3.connect(DATABASE_FILE)
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(agents)")}
        now = datetime.datetime.utcnow().isoformat(sep=" ")
        if "created_at" not in cols:
            conn.execute(f"ALTER TABLE agents ADD COLUMN created_at DATETIME DEFAULT '{now}'")
        if "updated_at" not in cols:
            conn.execute(f"ALTER TABLE agents ADD COLUMN updated_at DATETIME DEFAULT '{now}'")
        conn.commit()
    finally:
        conn.close()


_migrate_agent_timestamps()


def _get_available_models() -> list:
    """Fetch the list of pulled model names from Ollama."""
    try:
        resp = httpx.get("http://localhost:11434/api/tags", timeout=5.0)
        if resp.status_code == 200:
            return [m["name"] for m in resp.json().get("models", [])]
    except Exception as e:
        logger.warning(f"Could not reach Ollama: {e}")
    return []


def _pick_model(available: list) -> str:
    """
    Pick the best model from what's actually installed.
    Preference: configured OLLAMA_MODEL → qwen → llama → deepseek → first available.
    """
    from app.config import settings

    if settings.OLLAMA_MODEL:
        if not available or settings.OLLAMA_MODEL in available:
            return settings.OLLAMA_MODEL
        # Prefix match for configured model (e.g. qwen3.5 vs qwen3.5:9b)
        cfg_base = settings.OLLAMA_MODEL.split(":")[0]
        for m in available:
            if m == settings.OLLAMA_MODEL or m.startswith(cfg_base):
                return m

    preference = [
        "qwen3.5:9b",
        "qwen3:8b",
        "qwen2.5:7b",
        "qwen2.5:14b",
        "qwen2.5",
        "qwen",
        "llama3.1:8b-instruct-q8_0",
        "llama3.1:8b",
        "deepseek-r1:8b",
    ]
    for p in preference:
        if p in available:
            return p
    for p in preference:
        base = p.split(":")[0]
        for m in available:
            if m.startswith(base):
                return m
    if available:
        return available[0]
    return settings.OLLAMA_MODEL


def sync_agent_models() -> None:
    """Update all agents to the configured / detected Ollama model on startup."""
    from app.config import settings

    db = SessionLocal()
    try:
        agents = db.query(AgentModel).all()
        if not agents:
            return

        available = _get_available_models()
        chosen = _pick_model(available)

        updated = 0
        for agent in agents:
            if agent.model != chosen:
                agent.model = chosen
                updated += 1

        if updated:
            db.commit()
            print(f"✅ Synced {updated} agent(s) to Ollama model: {chosen}")
            logger.info(f"Agent models synced to: {chosen}")
        elif available:
            logger.info(f"Agents already using model: {chosen}")

        if settings.ROUTING_MODEL != chosen and settings.ROUTING_MODEL == settings.OLLAMA_MODEL:
            logger.info(f"Routing model: {settings.ROUTING_MODEL}")

    except Exception as e:
        logger.error(f"Agent model sync failed: {e}")
        db.rollback()
    finally:
        db.close()


USER_SENDER_WEB = "Human (Web)"
USER_SENDER_TELEGRAM = "Human (Telegram)"

# Agents whose DB rows are not user-facing replies (routing / registry only).
INTERNAL_MESSAGE_SENDERS = frozenset({"Orchestrator Router"})


def _load_deleted_default_agents(db) -> set[str]:
    row = (
        db.query(PlatformMetaModel)
        .filter(PlatformMetaModel.key == DELETED_DEFAULT_AGENTS_META_KEY)
        .first()
    )
    if not row or not row.value:
        return set()
    try:
        data = json.loads(row.value)
        return {str(x) for x in data if x}
    except json.JSONDecodeError:
        return set()


def mark_default_agent_deleted(agent_id: str) -> None:
    """Remember that the user removed an optional seeded agent — do not re-create on restart."""
    if agent_id not in OPTIONAL_DEFAULT_AGENT_IDS:
        return
    db = SessionLocal()
    try:
        deleted = _load_deleted_default_agents(db)
        if agent_id in deleted:
            return
        deleted.add(agent_id)
        row = (
            db.query(PlatformMetaModel)
            .filter(PlatformMetaModel.key == DELETED_DEFAULT_AGENTS_META_KEY)
            .first()
        )
        payload = json.dumps(sorted(deleted))
        if row:
            row.value = payload
        else:
            db.add(PlatformMetaModel(key=DELETED_DEFAULT_AGENTS_META_KEY, value=payload))
        db.commit()
        logger.info(f"Optional default agent marked deleted: {agent_id}")
    except Exception as e:
        logger.error(f"mark_default_agent_deleted failed: {e}")
        db.rollback()
    finally:
        db.close()


def _build_default_agent(agent_id: str, chosen: str) -> AgentModel | None:
    """Factory for seeded agent rows."""
    specs: dict[str, dict] = {
        "agent_router": {
            "name": "Orchestrator Router",
            "role": "System Coordinator & Planner",
            "system_prompt": (
                "You are a routing supervisor. Your job is ONLY to classify intent and "
                "select the correct workflow — never answer the user's question directly. "
                "Output routing decisions as brief structured notes for downstream agents."
            ),
            "tools": "math_evaluator,math_solver,code_reviewer,security_scanner",
            "schedules": "continuous",
            "memory_window": 10,
        },
        "agent_specialist": {
            "name": "Reasoning Specialist",
            "role": "Deep-Dive Execution Engine",
            "system_prompt": (
                "You are an expert technical analyst. Process tasks with strict "
                "step-by-step reasoning. Provide clear, accurate, concise answers."
            ),
            "tools": "math_evaluator,math_solver",
            "schedules": "on_demand",
            "memory_window": 5,
        },
        "agent_code_reviewer": {
            "name": "Code Review Specialist",
            "role": "Security & Code Quality Analyst",
            "system_prompt": (
                "You are a senior security engineer and code reviewer. Analyze code for "
                "vulnerabilities, quality issues, and provide actionable remediation steps."
            ),
            "tools": "code_reviewer,security_scanner",
            "schedules": "on_demand",
            "memory_window": 8,
        },
        "agent_trip_planner": {
            "name": "Trip Planner",
            "role": "Travel & Itinerary Specialist",
            "system_prompt": (
                "You are an expert travel planner. Create practical itineraries with "
                "day-by-day plans, transport tips, and activity suggestions. "
                "Remember destination and preferences from earlier messages in the same conversation."
            ),
            "tools": "none",
            "skills": "trip planning, itineraries, flights, hotels, local activities, tourism",
            "schedules": "on_demand",
            "memory_window": 10,
            "channels": "web,telegram",
        },
    }
    spec = specs.get(agent_id)
    if not spec:
        return None
    return AgentModel(id=agent_id, model=chosen, **spec)


def _repair_builtin_agent_profiles(db) -> None:
    """Fix common DB drift — e.g. Trip Planner missing telegram channel."""
    repairs = {
        "agent_trip_planner": {
            "channels": "web,telegram",
            "skills": "trip planning, itineraries, flights, hotels, local activities, tourism",
        },
        "agent_router": {
            "skills": "routing, planning, task decomposition",
        },
        "agent_specialist": {
            "skills": "reasoning, analysis, general Q&A",
        },
        "agent_code_reviewer": {
            "skills": "code review, security auditing, vulnerability analysis",
        },
    }
    changed = 0
    for agent_id, fields in repairs.items():
        agent = db.query(AgentModel).filter(AgentModel.id == agent_id).first()
        if not agent:
            continue
        for key, value in fields.items():
            current = getattr(agent, key, "") or ""
            if key == "channels" and "telegram" not in current and agent_id == "agent_trip_planner":
                agent.channels = value
                changed += 1
            elif key == "skills" and current.strip() in ("", "Code analysis, structural auditing"):
                agent.skills = value
                changed += 1

    for agent in db.query(AgentModel).all():
        blob = f"{agent.id} {agent.name} {agent.role} {agent.skills or ''}".lower()
        if any(k in blob for k in ("trip", "travel", "planner", "itinerary")):
            channels = [c.strip() for c in (agent.channels or "web").split(",") if c.strip()]
            if "telegram" not in channels:
                channels.append("telegram")
                agent.channels = ",".join(dict.fromkeys(channels))
                changed += 1

    if changed:
        db.commit()
        logger.info(f"Repaired {changed} agent profile field(s) for Telegram/runtime.")


def ensure_default_agents() -> None:
    """Re-create missing core agents only. Optional demo agents respect user deletion."""
    db = SessionLocal()
    try:
        available = _get_available_models()
        chosen = _pick_model(available)
        deleted_optional = _load_deleted_default_agents(db)

        candidate_ids = list(CORE_DEFAULT_AGENT_IDS)
        for agent_id in OPTIONAL_DEFAULT_AGENT_IDS:
            if agent_id not in deleted_optional:
                candidate_ids.append(agent_id)

        added = 0
        for agent_id in candidate_ids:
            if db.query(AgentModel).filter(AgentModel.id == agent_id).first():
                continue
            default = _build_default_agent(agent_id, chosen)
            if default:
                db.add(default)
                added += 1

        if added:
            db.commit()
            print(f"✅ Added {added} missing default agent(s).")
            logger.info(f"Ensured default agents — added {added}.")

        _repair_builtin_agent_profiles(db)
    except Exception as e:
        logger.error(f"ensure_default_agents failed: {e}")
        db.rollback()
    finally:
        db.close()


def seed_database():
    db = SessionLocal()
    try:
        if db.query(AgentModel).first():
            logger.info("Database already seeded — skipping.")
            return

        available = _get_available_models()
        chosen    = _pick_model(available)

        if available:
            print(f"✅ Ollama models found: {available}")
            print(f"✅ Using model: {chosen}")
            logger.info(f"Seeding agents with model: {chosen}")
        else:
            print(f"⚠️  Ollama unreachable — defaulting to {chosen}. Start with: ollama serve")
            logger.warning(f"Ollama unreachable, seeding with fallback model: {chosen}")

        seed_ids = list(CORE_DEFAULT_AGENT_IDS) + list(OPTIONAL_DEFAULT_AGENT_IDS)
        db.add_all([
            agent for agent_id in seed_ids
            if (agent := _build_default_agent(agent_id, chosen)) is not None
        ])
        db.commit()
        print("✅ Database seeded: router + specialist + code reviewer + trip planner agents created.")
        logger.info("Database seeded successfully.")

    except Exception as e:
        logger.error(f"Database seed failed: {e}")
        db.rollback()
    finally:
        db.close()