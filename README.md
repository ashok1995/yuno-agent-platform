# Yuno AI Agent Orchestration Platform

A **local-first multi-agent platform** built for the [Yuno AI Engineer Hiring Challenge](https://yuno.ai). Create and configure AI agents, connect them into collaborative workflows, execute real tools, monitor runs in real time, and chat via **Telegram** — all running on **Ollama** with no cloud API keys.

---

## What this project delivers

| Challenge area | Implementation |
|----------------|----------------|
| **Agent CRUD** | Full create / read / update / delete in **Agent Profiles** tab |
| **Agent configuration** | System prompt, model, tools, channels, schedules, memory window, skills, interaction rules, guardrails |
| **Visual workflow builder** | Load template → edit nodes & edges → save custom definitions |
| **Conditions & feedback loops** | Edge conditions (`always`, `computation_ok`, `scan_has_critical`) + code-review CRITICAL → re-plan loop |
| **Pre-built templates** | 4 built-in: auto-router, code review, financial analysis, direct answer |
| **External channel** | **Telegram** bot (same pipeline as web UI) |
| **Live monitoring** | Streaming execution feed, sidebar agent status, token/cost metrics |
| **Async agent communication** | Background jobs + event polling |
| **Persisted message history** | SQLite `message_logs` + **Persisted messages** / **Conversation** UI tabs |
| **Real runtime** | Ollama LLM + deterministic tools (not a mock UI) |
| **Tests** | 72 pytest tests (CRUD, routing, workflows, tools, registry, memory) |
| **Single setup command** | `./start.sh` |
| **Documentation** | This README — architecture, setup, extension guide |

### Submission checklist (you still need to record)

| Deliverable | Status |
|-------------|--------|
| Working Git repository | ✅ |
| README (architecture + setup + justification) | ✅ |
| **Demo video or GIF** (web + Telegram conversation) | ⬜ **Record & attach** |
| **Live demo session** with Yuno team | ⬜ **Schedule** |

**Evaluation weights (from assignment):** end-to-end demo 40% · architecture/code 30% · UI/UX 20% · documentation 10%.

---

## Quick start (single command)

### Prerequisites

- **Python 3.11+**
- **[Ollama](https://ollama.com)** running locally with at least one model:

```bash
ollama serve          # if not already running
ollama pull qwen3.5:9b
```

### Run

```bash
chmod +x start.sh stop.sh
./start.sh
```

| Service | URL |
|---------|-----|
| **Dashboard** | http://localhost:8501 |
| **API docs** | http://localhost:8000/docs |
| **Logs** | `logs/backend.log`, `logs/frontend.log` |

Stop: `./stop.sh`

On first run, `backend/.env` is copied from `.env.example`. Edit it before restart if you use Telegram.

---

## Environment variables

Copy `backend/.env.example` → `backend/.env`:

| Variable | Default | Purpose |
|----------|---------|---------|
| `OLLAMA_URL` | `http://127.0.0.1:11434/api/generate` | Local LLM endpoint |
| `OLLAMA_MODEL` | `qwen3.5:9b` | Default agent model |
| `ROUTING_MODEL` | `qwen3.5:9b` | Intent classifier model |
| `OLLAMA_THINK` | `false` | Qwen thinking mode (off = visible tokens in feed) |
| `TELEGRAM_BOT_TOKEN` | *(empty)* | Bot token from [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_REPLY_TIMEOUT_SEC` | `120` | Max wait before Telegram timeout message |
| `LOG_LEVEL` | `INFO` | File + stderr log level (`DEBUG`, `INFO`, …) |
| `LOG_FILE_PATH` | `logs/backend.log` | Backend log file (routing, agents, Telegram) |
| `API_HOST` / `API_PORT` | `127.0.0.1` / `8000` | FastAPI bind |
| `DATABASE_PATH` | `backend/platform.db` | SQLite path (agents + messages persist here) |

**Telegram setup**

1. Message [@BotFather](https://t.me/BotFather) → `/newbot` → copy token.
2. Set `TELEGRAM_BOT_TOKEN=<your-token>` in `backend/.env`.
3. `./stop.sh && ./start.sh`
4. Backend log should show `Telegram channel live`.
5. Message your bot — uses the **same multi-agent pipeline** as the web dashboard.

**See logs while testing Telegram**

| Where | What you see |
|-------|----------------|
| **Terminal** | `tail -f logs/backend.log` — routing, agent calls, inbound/outbound Telegram lines |
| **Dashboard sidebar** | **📱 Telegram Activity** — live events for `tg_<chat_id>` workflows |
| **Runtime → Persisted messages** | Click **Load**, filter workflow `tg_<your_chat_id>` for full inter-agent thread |

Example log lines after messaging the bot:

```text
yuno.telegram | Telegram inbound — chat_id=123 workflow=tg_123 text='Hi'
yuno.telegram | Telegram message routed — workflow=tg_123 template=direct_answer ...
yuno.telegram | Telegram reply sent — workflow=tg_123 chars=42
```

Each Telegram chat gets a stable conversation id (`tg_<chat_id>`) so multi-turn context is preserved.

---

## Using the dashboard

Open http://localhost:8501. Main tabs:

| Tab | Purpose |
|-----|---------|
| **Runtime Dashboard** | Enter a task → **Deploy** → watch live feed + conversation thread |
| **Agent Profiles** | Create / edit / delete agents (all 10+ config fields) |
| **Workflow Builder** | Load built-in template, modify graph, save custom workflow |
| **Persisted messages** | Full inter-agent message log from SQLite |

### Suggested demo flow (for video)

1. **Agent CRUD** — create a custom agent (e.g. Finance Analyst), set system prompt + tools + channels.
2. **Finance workflow** — `1000 invested at 5% annual return — what after 5 years?` → FV ≈ **$1,276.28**
3. **Code review + feedback loop** — `Audit: def run(): return eval(input())` → security scan → CRITICAL → router re-plans
4. **Multi-turn trip** — Turn 1: `Plan a 3-day trip to Dharamshala` → Turn 2: `Add paragliding on day 2` (same conversation)
5. **Telegram** — repeat greeting + trip in the bot; show it remembers context within the chat

### Example prompts

| Prompt | Route | Specialist (registry) |
|--------|-------|------------------------|
| `Hi how are you` | direct_answer (fast greeting path) | Reasoning Specialist |
| `1000 at 5% for 5 yrs` | financial_analysis | Finance-capable agent |
| `Audit: eval(input())` | code_review_loop | Code Review Specialist |
| `Plan a weekend in London` | direct_answer | Trip Planner |
| `What is 2+2?` | direct_answer | Reasoning Specialist |

Enable **Continue conversation** on the dashboard to reuse the same `workflow_id` across turns (like Telegram).

---

## Architecture

```mermaid
flowchart TB
    subgraph clients [Clients]
        UI[Streamlit Dashboard :8501]
        TG[Telegram Bot]
    end

    subgraph api [FastAPI Backend :8000]
        CRUD[/agents CRUD/]
        WF[POST /workflows/run]
        EVT[GET /events]
        MSG[/messages/]
        DEF[/workflows/definitions/]
    end

    subgraph runtime [Agent Runtime]
        RTR[Orchestration Router\nheuristics + Ollama intent]
        REG[Agent Registry\nDB-backed specialist pick]
        EXEC[Workflow Executor\ncode_review | financial | direct | custom]
        MEM[Conversation Memory\nper workflow_id]
        TOOLS[Deterministic Tools\nmath | security | code quality]
    end

    subgraph infra [Infrastructure]
        OLL[Ollama :11434]
        DB[(SQLite platform.db)]
    end

    UI --> CRUD & WF & EVT & MSG & DEF
    TG --> WF
    WF --> RTR --> EXEC
    EXEC --> REG
    EXEC --> TOOLS
    EXEC --> MEM
    EXEC --> OLL
    RTR --> OLL
    EXEC --> DB
    EXEC --> EVT
    REG --> DB
```

### Layer separation

| Layer | Location | Responsibility |
|-------|----------|----------------|
| **UI** | `frontend/` | Streamlit dashboard, live polling, CRUD, workflow builder |
| **API** | `backend/app/main.py` | REST endpoints, background workflow jobs |
| **Router** | `router_service.py` | Picks workflow template (rules → Ollama JSON) |
| **Registry** | `agent_registry.py` | Picks which DB agent fills orchestrator/specialist slots |
| **Executor** | `workflow_executor.py`, `graph.py` | Runs graph nodes, tools, feedback loops |
| **Memory** | `agent_runtime.py` | Builds multi-turn context per `workflow_id` |
| **Engine** | `engine.py` | Ollama streaming + event emission |
| **Persistence** | `database.py` | Agents, message logs, custom workflow definitions |
| **Channels** | `channel.py` | Telegram → same pipeline as web |

### Runtime choice (justification)

We use a **custom async workflow runtime** (inspired by LangGraph) instead of LangGraph/CrewAI/AutoGen directly:

| Reason | Detail |
|--------|--------|
| **Local-first** | 100% Ollama — no OpenAI/Anthropic keys required |
| **Event streaming** | Fine-grained `tool`, `agent`, `system` events pushed to Streamlit in ~60ms polls |
| **Deterministic tools** | Financial FV, security scan, code review run without LLM hallucination |
| **Registry pattern** | Workflow graphs use generic `orchestrator` / `specialist` slots; agents are swappable via DB |
| **Trade-off** | Streamlit form-based graph editor (not drag-and-drop canvas like LangGraph Studio) |

### Tech stack

| Choice | Why |
|--------|-----|
| **Python** | Native AI/ML ecosystem, FastAPI async, rapid prototyping |
| **FastAPI + SQLAlchemy** | Clean API, background tasks, SQLite with zero config |
| **Streamlit** | Fast visual UI without React overhead — ideal for hiring challenge timeline |
| **Ollama** | Local LLM runtime, full control, no cloud cost |
| **Telegram** | Simplest external channel vs WhatsApp Business API or Slack app setup |

### Default agents (seeded on startup)

| ID | Name | Channels | Role |
|----|------|----------|------|
| `agent_router` | Orchestrator Router | web, telegram | Task decomposition & routing |
| `agent_specialist` | Reasoning Specialist | web, telegram | General Q&A |
| `agent_code_reviewer` | Code Review Specialist | web, telegram | Security & code quality |
| `agent_trip_planner` | Trip Planner | web, telegram | Travel itineraries |

Agents persist in `backend/platform.db` across restarts. Tests use isolated `platform_test.db` (`YUNO_ENV=test`).

### Built-in workflow templates

| Template ID | Flow |
|-------------|------|
| `dynamic_router_intent` | Auto-classify prompt → dispatch to built-in subgraph |
| `code_review_loop` | Router → `code_reviewer` → `security_scanner` → Specialist → **feedback loop** on CRITICAL |
| `financial_analysis` | Router → `math_solver` (compound FV) → Specialist summary |
| `direct_answer` | Specialist only (trip, greetings, general Q&A) |

Custom workflows saved from the builder are stored in SQLite and appear in the template list.

### Tools (deterministic)

| Tool | Purpose |
|------|---------|
| `math_evaluator` | Safe arithmetic expressions |
| `math_solver` | Compound interest + natural-language finance parsing |
| `code_reviewer` | Style, complexity, quality notes |
| `security_scanner` | Static vulnerability patterns (eval, SQLi, pickle, etc.) |

General knowledge and open-ended replies use **Ollama** — not hardcoded lookup tables.

### Routing strategy

1. **Small-talk fast path** — greetings skip Ollama routing (instant Telegram reply)
2. **Keyword heuristics** — finance / trip / security keywords (fast, reliable)
3. **Ollama JSON classifier** — ambiguous prompts
4. **Agent registry** — current message prioritized over full history when picking specialist (topic switches work)

### Feedback loop — how to verify

1. Run **Code Review & Security Audit** with: `def run(): return eval(input())`
2. Execution feed shows `feedback_loop` after `security_scanner` reports CRITICAL
3. Persisted messages show **two** Router and **two** Specialist entries

---

## API reference

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/agents` | List agents |
| `POST` | `/agents` | Create or update agent |
| `DELETE` | `/agents/{id}` | Delete agent |
| `GET` | `/tools` | List registered tools |
| `POST` | `/workflows/run` | Queue workflow (returns **202** immediately) |
| `GET` | `/events` | Poll live execution events |
| `DELETE` | `/events` | Clear in-memory event buffer |
| `GET` | `/workflows/templates` | Built-in + custom templates |
| `GET` | `/workflows/templates/{id}` | Template detail + Mermaid |
| `POST` | `/workflows/definitions` | Save custom workflow from builder |
| `GET` | `/messages` | Persisted inter-agent messages (`?workflow_id=`) |

---

## Manual setup (alternative to `./start.sh`)

### Backend

```bash
cd backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

### Frontend

```bash
cd frontend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

---

## Tests

All tests run **in-process** — no live server or Ollama required (LLM calls mocked).

```bash
cd backend
source venv/bin/activate
PYTHONPATH=. pytest app/tests/ -v
```

| Module | Coverage |
|--------|----------|
| `test_critical_paths.py` | Agent CRUD, workflow 202, events, messages API |
| `test_workflow_integration.py` | End-to-end workflows → DB + `WORKFLOW_COMPLETE` |
| `test_agents_crud.py` | Agent persistence fields |
| `test_agent_registry.py` | Specialist routing, topic switch, channels |
| `test_agent_runtime.py` | Multi-turn conversation context |
| `test_router.py` | Intent routing, greeting fast path |
| `test_financial_parser.py` | Compound interest parsing |
| `test_tools.py` | Math + security + code review tools |
| `test_workflow_definitions.py` | Custom graph save/load |

---

## Extending the platform

### Add a workflow template

1. Define graph in `backend/app/workflow_definitions.py` (nodes, edges, conditions).
2. Add routing label in `router_service.py` (`SPECIALTY_PROMPT_HINTS` + LLM prompt).
3. Implement any template-specific logic in `workflow_executor.py`.
4. Optional: add UI preset in `frontend/components/runtime_dashboard.py`.

### Add a messaging channel (Slack / WhatsApp)

1. Copy the pattern in `backend/app/channel.py`:
   - Receive message → stable `workflow_id` per user/chat
   - `build_conversation_context()` for multi-turn memory
   - `resolve_topology()` → `execute_workflow_pipeline()` in background
   - Poll `_latest_agent_reply()` from `message_logs`
2. Filter agents by `channels` field in the registry.
3. Register channel startup in `main.py` `startup_event`.

### Add a tool

1. Implement handler in `backend/app/tools.py` and register in `_TOOL_SPECS`.
2. Expose via `GET /tools` (automatic).
3. Assign tool name to agent's `tools` field in Agent Profiles.

---

## Project layout

```
yuno-agent-platform/
├── start.sh / stop.sh          # Single-command lifecycle
├── logs/                       # backend.log, frontend.log
├── backend/
│   ├── app/
│   │   ├── main.py             # FastAPI endpoints
│   │   ├── graph.py            # Workflow pipeline entry
│   │   ├── workflow_executor.py
│   │   ├── workflow_definitions.py
│   │   ├── router_service.py   # Intent routing
│   │   ├── agent_registry.py   # DB-backed agent selection
│   │   ├── agent_runtime.py    # Conversation memory
│   │   ├── financial_parser.py
│   │   ├── engine.py           # Ollama streaming + events
│   │   ├── channel.py          # Telegram integration
│   │   ├── tools.py
│   │   ├── database.py         # SQLite models + seed
│   │   └── tests/
│   ├── platform.db             # Production DB (gitignored)
│   └── .env.example
└── frontend/
    ├── app.py
    └── components/
        ├── runtime_dashboard.py
        ├── agent_crud.py
        ├── workflow_builder.py
        └── metrics.py
```

---

## Known limitations (discuss in live demo)

| Topic | Reality |
|-------|---------|
| Workflow builder UI | Form-based node/edge editor, not a drag-and-drop canvas |
| Schedules | `on_demand` / `continuous` enforced; cron-style scheduling not implemented |
| Auto-router | Classifier dispatches to built-in subgraphs; fully custom graphs run when explicitly selected/saved |
| Telegram | Requires valid bot token + Ollama running; first LLM call may take 10–30s on cold start |
| Model dependency | Quality and speed depend on locally installed Ollama model |

---

## Recording your demo (Mac)

1. **⌘ + Shift + 5** → Record Selected Portion or Entire Screen.
2. Record **web**: agent CRUD → finance task → code review → multi-turn trip with conversation panel.
3. Record **Telegram**: `/start`, greeting, trip plan in the same chat.
4. Export as MP4 or GIF and attach to your submission.

---

## License

Confidential — Yuno AI Engineer Hiring Challenge submission.
