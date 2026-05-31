import streamlit as st
import httpx
import time

from components.ui_theme import inject_global_theme
from components.ui_state import apply_pending_runtime_ui

st.set_page_config(
    page_title="Yuno Agent Platform",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

inject_global_theme()
apply_pending_runtime_ui()

# ─── STATE INITIALIZATION ──────────────────────────────────────────────────────
for key, default in [
    ("logs",                     []),
    ("active_agent",             "Idle"),
    ("metrics",                  {"tokens": 0, "cost": 0.0}),
    ("agent_outputs",            {}),
    ("execution_feed",           []),
    ("is_processing",            False),
    ("current_workflow_id",      None),
    ("last_event_count",         0),
    ("_workflow_submitted",      False),
    ("_processing_started_at",   0.0),
    ("_seen_agent_starts",       set()),
    ("_session_started",         False),
    ("_active_tools",            set()),
    ("conversation_workflow_id", None),
    ("continue_conversation",    True),
    ("selected_template_id",     "dynamic_router_intent"),
    ("prompt_input_raw",         ""),
]:
    if key not in st.session_state:
        st.session_state[key] = default

BACKEND_URL  = "http://localhost:8000"
MAX_WAIT_SEC = 180
POLL_INTERVAL = 0.06  # faster polling for smoother streaming


def _fetch_sidebar_agents() -> list[dict]:
    try:
        resp = httpx.get(f"{BACKEND_URL}/agents", timeout=3.0)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return []


def _render_sidebar_agents():
    agents = _fetch_sidebar_agents()
    if not agents:
        st.caption("No agents loaded — start backend on :8000")
        return

    styles = {
        "Orchestrator Router": "#7ae0a0",
        "Reasoning Specialist": "#c0a0f0",
        "Code Review Specialist": "#f0a0a0",
        "Trip Planner": "#fcd080",
    }
    lines = []
    for a in agents:
        name = a.get("name", a.get("id", "agent"))
        color = styles.get(name, "#a0d0f0")
        icon = "◈" if "Router" in name else "◉" if "Specialist" in name else "◦"
        lines.append(f'<span style="color:{color};">{icon} {name}</span>')

    st.markdown(
        '<div style="display:flex;flex-direction:column;gap:8px;font-size:12px;">'
        + "".join(lines)
        + "</div>",
        unsafe_allow_html=True,
    )


def poll_telegram_events():
    """Show Telegram bot activity in the sidebar even when the web UI is idle."""
    if "telegram_logs" not in st.session_state:
        st.session_state.telegram_logs = []
    if "telegram_event_count" not in st.session_state:
        st.session_state.telegram_event_count = 0

    had_new = False
    try:
        resp = httpx.get(f"{BACKEND_URL}/events", timeout=2.0)
        all_events = resp.json().get("events", [])
        new_events = all_events[st.session_state.telegram_event_count:]
        if not new_events:
            return had_new

        st.session_state.telegram_event_count = len(all_events)
        for payload in new_events:
            data = payload.get("data", {})
            workflow_id = data.get("workflow_id", "")
            if not str(workflow_id).startswith("tg_"):
                continue

            had_new = True
            event_type = payload.get("type")
            if event_type == "USER_MESSAGE":
                preview = (data.get("content") or "")[:80]
                st.session_state.telegram_logs.append(f"📩 {preview}")
            elif event_type == "AGENT_START":
                name = data.get("agent_name", "Agent")
                st.session_state.telegram_logs.append(f"🟢 {name} ({workflow_id})")
            elif event_type == "WORKFLOW_COMPLETE":
                st.session_state.telegram_logs.append(f"🏁 Done ({workflow_id})")
    except Exception:
        pass
    return had_new


def _render_sidebar():
    """Left sidebar — agents, runtime metrics, tools, event log."""
    from components.stream_panel import render_sidebar_metrics, render_tool_badges

    with st.sidebar:
        st.markdown("### 🤖 Agents")
        _render_sidebar_agents()
        st.markdown("---")
        st.markdown("### 📊 Runtime")
        render_sidebar_metrics(st.session_state.is_processing)
        st.markdown("---")
        render_tool_badges()
        st.markdown("---")
        st.markdown("### 📋 Event Log")
        if st.session_state.logs:
            for log in reversed(st.session_state.logs[-30:]):
                st.caption(log)
        else:
            st.caption("No events yet.")
        st.markdown("---")
        st.markdown("### 📱 Telegram Activity")
        tg_logs = st.session_state.get("telegram_logs") or []
        if tg_logs:
            for log in reversed(tg_logs[-20:]):
                st.caption(log)
        else:
            st.caption("Message your bot — activity appears here and in logs/backend.log.")


def _stop_processing():
    st.session_state.active_agent        = "Idle"
    st.session_state.is_processing       = False
    st.session_state._workflow_submitted = False
    st.session_state._active_tools       = set()
    try:
        httpx.delete(f"{BACKEND_URL}/events", timeout=2.0)
    except Exception:
        pass
    st.session_state.last_event_count = 0
    st.session_state.telegram_event_count = 0


def poll_and_apply_events():
    if not st.session_state.is_processing or not st.session_state.get("_session_started"):
        return

    elapsed = time.time() - st.session_state._processing_started_at
    if elapsed > MAX_WAIT_SEC:
        st.session_state.logs.append("⏱️ Workflow timed out.")
        _stop_processing()
        return

    try:
        resp = httpx.get(f"{BACKEND_URL}/events", timeout=2.0)
        all_events = resp.json().get("events", [])
        new_events = all_events[st.session_state.last_event_count:]
        if not new_events:
            return

        st.session_state.last_event_count = len(all_events)
        completed = False

        for payload in new_events:
            m_type = payload.get("type")
            data   = payload.get("data", {})

            if m_type == "USER_MESSAGE":
                sender = data.get("sender", "Human (Web)")
                content = data.get("content", "")
                if not any(
                    e.get("role") == sender and e.get("content") == content
                    for e in st.session_state.execution_feed
                ):
                    st.session_state.execution_feed.append({
                        "role": sender,
                        "content": content,
                        "streaming": False,
                    })

            elif m_type == "AGENT_START":
                name = data.get("agent_name", "")
                st.session_state.active_agent = name

                if name not in st.session_state._seen_agent_starts:
                    st.session_state._seen_agent_starts.add(name)
                    st.session_state.logs.append(f"🟢 {name} activated")
                    st.session_state.execution_feed.append({
                        "role": "system",
                        "content": f"🏁 Node Activation: {name} initialized.",
                        "streaming": False,
                    })

                if name not in st.session_state.agent_outputs:
                    st.session_state.agent_outputs[name] = ""

            elif m_type == "TOOL_START":
                tool_name = data.get("tool", "")
                st.session_state._active_tools.add(tool_name)
                st.session_state.logs.append(f"⚙ Running tool: {tool_name}...")
                st.session_state.execution_feed.append({
                    "role": "tool",
                    "content": f"⏳ Tool running: **{tool_name}**",
                    "streaming": True,
                    "tool": tool_name,
                })

            elif m_type == "TOKEN_STREAM":
                token   = data.get("token", "")
                kind    = data.get("stream_kind", "response")
                metrics = data.get("metrics") or {}
                if metrics:
                    st.session_state.metrics["tokens"] = metrics.get(
                        "total_tokens", st.session_state.metrics["tokens"]
                    )
                    st.session_state.metrics["cost"] = metrics.get(
                        "cost", st.session_state.metrics["cost"]
                    )
                name = data.get("agent_name") or st.session_state.active_agent
                if name and name not in ("Idle", "Starting...", "Queued..."):
                    st.session_state.active_agent = name
                    st.session_state.agent_outputs[name] = (
                        st.session_state.agent_outputs.get(name, "") + token
                    )
                    feed = st.session_state.execution_feed
                    feed_role = name if kind == "response" else f"{name} (thinking)"
                    if feed and feed[-1].get("role") == feed_role and feed[-1].get("streaming"):
                        feed[-1]["content"] += token
                    else:
                        feed.append({
                            "role": feed_role,
                            "content": token,
                            "streaming": True,
                        })

            elif m_type == "TOOL_EXECUTION":
                tool_name = data.get("tool", "")
                result    = data.get("result", "")
                st.session_state._active_tools.discard(tool_name)
                st.session_state.logs.append(f"🛠️ Tool done: {tool_name}")
                # Replace running placeholder if present
                feed = st.session_state.execution_feed
                replaced = False
                for entry in reversed(feed):
                    if entry.get("tool") == tool_name and entry.get("streaming"):
                        entry["content"] = f"🛠️ **{tool_name}**\n{result}"
                        entry["streaming"] = False
                        entry.pop("tool", None)
                        replaced = True
                        break
                if not replaced:
                    feed.append({
                        "role": "tool",
                        "content": f"🛠️ **{tool_name}**\n{result}",
                        "streaming": False,
                    })

            elif m_type == "WORKFLOW_COMPLETE":
                for entry in st.session_state.execution_feed:
                    entry["streaming"] = False
                st.session_state.logs.append("🏁 Workflow completed.")
                completed = True

        if completed:
            _stop_processing()

    except Exception as e:
        st.session_state.logs.append(f"⚠️ Poll error: {e}")


if not st.session_state.is_processing and st.session_state.active_agent in ("Starting...", "Queued..."):
    st.session_state.active_agent = "Idle"

poll_and_apply_events()
telegram_had_new = poll_telegram_events()

# Sidebar must render before main content so Streamlit keeps it visible.
_render_sidebar()

from components.metrics import render_global_metrics
from components.runtime_dashboard import render_runtime_tab
from components.agent_crud import render_crud_tab
from components.workflow_builder import render_workflow_builder_tab

render_global_metrics()

tab_run, tab_builder, tab_crud = st.tabs([
    "🎮 Runtime Dashboard",
    "🔀 Workflow Builder",
    "🛠️ Agent Profiles",
])

with tab_run:
    render_runtime_tab()

with tab_builder:
    render_workflow_builder_tab()

with tab_crud:
    render_crud_tab()

if st.session_state.is_processing:
    time.sleep(POLL_INTERVAL)
    st.rerun()
elif telegram_had_new:
    time.sleep(0.25)
    st.rerun()
