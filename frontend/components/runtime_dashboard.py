import streamlit as st
import httpx
import uuid
import time

from components.ui_state import apply_pending_runtime_ui, queue_runtime_ui

BACKEND_URL = "http://localhost:8000"


def _inject_css():
    st.markdown("""
    <style>
    .feed-card {
        border-radius: 10px;
        padding: 12px 16px;
        margin-bottom: 10px;
        font-family: 'JetBrains Mono', 'Courier New', monospace;
        font-size: 13px;
        line-height: 1.6;
        white-space: pre-wrap;
        word-break: break-word;
        border: 1px solid;
    }
    .feed-card .card-header {
        font-size: 10px;
        font-weight: 600;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        margin-bottom: 7px;
    }
    .card-system      { background:#0a1628; border-color:#1a3a6a; color:#8ab4f8; }
    .card-system .card-header { color:#4a8af4; }
    .card-tool        { background:#1a1200; border-color:#5a4000; color:#fcd080; }
    .card-tool .card-header   { color:#f0a020; }
    .card-tool-stream { background:#1a1500; border-color:#7a6000; color:#fde68a;
                        border-style: dashed; animation: toolpulse 1.5s ease-in-out infinite; }
    @keyframes toolpulse { 0%,100%{ border-color:#7a6000; } 50%{ border-color:#f0a020; } }
    .card-router      { background:#0a2010; border-color:#1a5a30; color:#7ae0a0; }
    .card-router .card-header { color:#2aaa60; }
    .card-specialist  { background:#150a28; border-color:#4a1a7a; color:#c0a0f0; }
    .card-specialist .card-header { color:#9a60e0; }
    .card-code-review { background:#1a0a0a; border-color:#7a1a1a; color:#f0a0a0; }
    .card-code-review .card-header { color:#e06060; }
    .card-human       { background:#0a1a28; border-color:#1a4a6a; color:#a0d0f0; }
    .card-human .card-header { color:#60a0e0; }
    .card-default     { background:#1a1a1a; border-color:#3a3a3a; color:#d0d0d0; }
    .card-default .card-header { color:#888; }
    .output-box {
        border-radius: 8px; padding: 14px;
        font-family: 'JetBrains Mono', 'Courier New', monospace;
        font-size: 13px; line-height: 1.7;
        white-space: pre-wrap; word-break: break-word;
        max-height: 420px; overflow-y: auto; border: 1px solid;
    }
    .msg-row {
        padding: 10px 14px; border-radius: 8px; margin-bottom: 6px;
        border: 1px solid #2a2a2a; background: #111;
        font-family: 'JetBrains Mono', 'Courier New', monospace;
        font-size: 12px; line-height: 1.5;
    }
    .msg-sender { font-size: 10px; font-weight: 600; letter-spacing: 0.06em;
                  text-transform: uppercase; color: #888; margin-bottom: 4px; }
    @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.3} }
    .pulse-dot { animation: pulse 1.2s infinite; display: inline-block; }
    .stream-cursor {
        display: inline-block; width: 8px; height: 1em; background: #86efac;
        margin-left: 2px; animation: blink 0.9s step-end infinite; vertical-align: text-bottom;
    }
    @keyframes blink { 50% { opacity: 0; } }
    .turn-divider {
        text-align: center; font-size: 10px; letter-spacing: 0.12em;
        text-transform: uppercase; color: #555; margin: 14px 0 10px;
        border-top: 1px dashed #333; padding-top: 10px;
    }
    .chat-scroll {
        max-height: 520px; overflow-y: auto; padding-right: 4px;
        margin-bottom: 16px;
    }
    """, unsafe_allow_html=True)


def _card_class(role: str) -> tuple:
    m = {
        "system":                  ("card-system",     "⬡ system"),
        "tool":                    ("card-tool",        "⚙ tool"),
        "Orchestrator Router":     ("card-router",      "◈ orchestrator router"),
        "Reasoning Specialist":    ("card-specialist",  "◉ reasoning specialist"),
        "Code Review Specialist":  ("card-code-review", "🔍 code review specialist"),
        "Human (Web)":             ("card-human",       "👤 you"),
        "Human (Telegram)":        ("card-human",       "👤 you (telegram)"),
    }
    if role.endswith(" (thinking)"):
        base = role.replace(" (thinking)", "")
        css, header = m.get(base, ("card-default", f"◦ {base.lower()}"))
        return css, f"{header} · thinking"
    return m.get(role, ("card-default", f"◦ {role.lower()}"))


def _output_style(role: str) -> tuple:
    m = {
        "Orchestrator Router":     ("#0a2010", "#1a5a30", "#7ae0a0"),
        "Reasoning Specialist":    ("#150a28", "#4a1a7a", "#c0a0f0"),
        "Code Review Specialist":  ("#1a0a0a", "#7a1a1a", "#f0a0a0"),
        "Human (Web)":             ("#0a1a28", "#1a4a6a", "#a0d0f0"),
    }
    return m.get(role, ("#111", "#333", "#b8ffb8"))


def _safe(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _hydrate_conversation_from_db(conv_id: str) -> None:
    """Load persisted thread into the feed (e.g. after page refresh)."""
    try:
        resp = httpx.get(
            f"{BACKEND_URL}/messages",
            params={"workflow_id": conv_id, "limit": 100},
            timeout=5.0,
        )
        if resp.status_code != 200:
            return
        msgs = sorted(resp.json(), key=lambda m: m.get("id", 0))
        st.session_state.execution_feed = [
            {
                "role": m.get("sender", "unknown"),
                "content": m.get("content", ""),
                "streaming": False,
                "msg_id": m.get("id"),
            }
            for m in msgs
        ]
    except Exception:
        pass


def _chat_roles_only(feed: list) -> list:
    """Conversation messages only — hide system noise and internal routing tools."""
    routing_tool_markers = ("Orchestrator Router", "Agent Registry", "Node Activation")
    filtered = []
    for e in feed:
        role = e.get("role", "")
        if role == "system":
            continue
        if role == "tool":
            content = e.get("content", "")
            if any(marker in content for marker in routing_tool_markers):
                continue
        filtered.append(e)
    return filtered


def _feed_entry_html(
    entry: dict,
    feed: list,
    streaming_idx: int | None,
    show_cursor: bool,
) -> str:
    role = entry.get("role", "system")
    content = entry.get("content", "")
    css, header = _card_class(role)
    if role == "tool" and entry.get("streaming"):
        css = "card-tool-stream"

    cursor = ""
    if (
        show_cursor
        and streaming_idx is not None
        and entry is feed[streaming_idx]
        and role != "tool"
    ):
        cursor = '<span class="stream-cursor"></span>'

    return (
        f'<div class="feed-card {css}"><div class="card-header">{header}</div>'
        f'{_safe(content)}{cursor}</div>'
    )


def _render_feed(feed: list, show_cursor: bool = False, newest_first: bool = True):
    if not feed:
        return

    streaming_idx = None
    if show_cursor:
        for i in range(len(feed) - 1, -1, -1):
            if feed[i].get("streaming"):
                streaming_idx = i
                break

    display = list(reversed(feed)) if newest_first else feed
    human_seen = 0
    parts: list[str] = []

    for entry in display:
        role = entry.get("role", "system")
        is_human = role in ("Human (Web)", "Human (Telegram)")
        if is_human:
            if human_seen > 0:
                parts.append('<div class="turn-divider">Earlier turn</div>')
            human_seen += 1
        parts.append(_feed_entry_html(entry, feed, streaming_idx, show_cursor))

    st.markdown("".join(parts), unsafe_allow_html=True)


def _conversation_html(feed: list, show_cursor: bool = False) -> str:
    if not feed:
        return ""

    streaming_idx = None
    if show_cursor:
        for i in range(len(feed) - 1, -1, -1):
            if feed[i].get("streaming"):
                streaming_idx = i
                break

    display = list(reversed(feed))
    human_seen = 0
    parts: list[str] = []

    for entry in display:
        role = entry.get("role", "system")
        if role in ("Human (Web)", "Human (Telegram)"):
            if human_seen > 0:
                parts.append('<div class="turn-divider">Earlier turn</div>')
            human_seen += 1
        parts.append(_feed_entry_html(entry, feed, streaming_idx, show_cursor))

    return f'<div class="chat-scroll">{"".join(parts)}</div>'


def _render_snapshots(agent_outputs: dict):
    for name, output in agent_outputs.items():
        if not output.strip():
            continue
        bg, border, color = _output_style(name)
        _, header = _card_class(name)
        with st.expander(header.upper(), expanded=True):
            st.markdown(
                f'<div class="output-box" style="background:{bg};border-color:{border};color:{color};">'
                f'{_safe(output)}</div>',
                unsafe_allow_html=True
            )


# ── Main entry ─────────────────────────────────────────────────────────────────
def render_runtime_tab():
    _inject_css()
    apply_pending_runtime_ui()

    for key, default in [
        ("is_processing", False),
        ("prompt_input_raw", ""),
        ("workflow_history", []),
        ("conversation_workflow_id", None),
        ("continue_conversation", True),
    ]:
        if key not in st.session_state:
            st.session_state[key] = default

    exec_tab, history_tab, db_tab = st.tabs([
        "▶  Execute",
        "📜  Session history",
        "🗄️  Persisted messages",
    ])

    with exec_tab:
        _render_execute_panel()
    with history_tab:
        _render_history_panel()
    with db_tab:
        _render_db_messages_panel()


def _render_conversation_thread(feed: list, is_proc: bool, conv_id: str | None):
    """Chat-style thread: newest messages on top, older turns below."""
    chat_feed = _chat_roles_only(feed)
    if not chat_feed and not is_proc:
        return

    st.markdown("#### 💬 Conversation")
    if conv_id:
        st.caption(f"Newest on top · older turns below · `{conv_id}`")
    else:
        st.caption("Newest on top · older turns below")

    if is_proc and not chat_feed:
        st.markdown(
            '<div style="display:flex;align-items:center;gap:10px;padding:14px 18px;'
            'background:rgba(34,197,94,0.08);border:1px solid rgba(34,197,94,0.3);border-radius:12px;'
            'font-size:13px;color:#86efac;font-family:monospace;">'
            '<span class="pulse-dot">⚡</span>&nbsp;Connecting to agent runtime...</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(_conversation_html(chat_feed, show_cursor=is_proc), unsafe_allow_html=True)


# ── Execute panel ──────────────────────────────────────────────────────────────
def _render_execute_panel():
    st.markdown("#### 🤖 Unified Autonomous Execution Core")
    st.markdown("")

    conv_id = st.session_state.get("conversation_workflow_id")
    is_proc = st.session_state.is_processing

    # Restore thread from DB when resuming a conversation after refresh
    if conv_id and not st.session_state.get("execution_feed") and not is_proc:
        _hydrate_conversation_from_db(conv_id)

    # Template picker (synced with Workflow Builder tab)
    if "selected_template_id" not in st.session_state:
        st.session_state.selected_template_id = "dynamic_router_intent"

    try:
        tpl_resp = httpx.get(f"{BACKEND_URL}/workflows/templates", timeout=3.0)
        templates = tpl_resp.json() if tpl_resp.status_code == 200 else []
    except Exception:
        templates = []

    tpl_map = {t["id"]: t["name"] for t in templates} if templates else {
        "dynamic_router_intent": "Auto-Route (Dynamic)",
        "code_review_loop": "Code Review & Security Audit",
        "financial_analysis": "Financial Analysis",
        "direct_answer": "Direct Answer",
    }

    st.selectbox(
        "Workflow template",
        options=list(tpl_map.keys()),
        format_func=lambda x: tpl_map.get(x, x),
        key="selected_template_id",
        help="Auto-Route picks the best workflow and specialist agent from your saved registry.",
    )

    st.caption("Quick-fill examples (optional):")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        if st.button("🔒 Security", disabled=st.session_state.is_processing, use_container_width=True):
            queue_runtime_ui(
                template_id="code_review_loop",
                prompt="Audit this route for vulnerability: eval(input())",
            )
            st.rerun()
    with c2:
        if st.button("📊 Finance", disabled=st.session_state.is_processing, use_container_width=True):
            queue_runtime_ui(
                template_id="financial_analysis",
                prompt="Calculate compound interest on $50,000 at 8% over 10 years",
            )
            st.rerun()
    with c3:
        if st.button("🧮 Math", disabled=st.session_state.is_processing, use_container_width=True):
            queue_runtime_ui(
                template_id="direct_answer",
                prompt="what is 847 * 293?",
            )
            st.rerun()
    with c4:
        if st.button("🌍 Knowledge", disabled=st.session_state.is_processing, use_container_width=True):
            queue_runtime_ui(
                template_id="direct_answer",
                prompt="What is the capital of France?",
            )
            st.rerun()

    user_prompt = st.text_area(
        "Task specification",
        height=90,
        key="prompt_input_raw",
        placeholder="Enter your directive — e.g. 'Review this Python function for security issues'"
    )

    conv_id = st.session_state.get("conversation_workflow_id")
    conv_col, new_col = st.columns([3, 1])
    with conv_col:
        st.checkbox(
            "Continue conversation",
            value=st.session_state.get("continue_conversation", True),
            key="continue_conversation",
            help="Reuse the same session so the agent remembers prior messages (destination, budget, etc.).",
            disabled=st.session_state.is_processing or not conv_id,
        )
        if conv_id:
            st.caption(f"Active conversation: `{conv_id}`")
    with new_col:
        if st.button(
            "🆕 New conversation",
            disabled=st.session_state.is_processing,
            use_container_width=True,
        ):
            st.session_state.conversation_workflow_id = None
            st.session_state.continue_conversation = True
            st.session_state.execution_feed = []
            st.session_state.agent_outputs = {}
            st.session_state.logs.append("─── Started new conversation ───")
            st.rerun()

    btn_label = "⏳ Executing..." if st.session_state.is_processing else "🚀 Deploy Autonomous Graph Loop"
    if st.button(btn_label, type="primary", disabled=st.session_state.is_processing, use_container_width=True):
        if not user_prompt.strip():
            st.warning("Please enter a task specification before deploying.")
        else:
            continue_conv = (
                st.session_state.get("continue_conversation", True)
                and st.session_state.get("conversation_workflow_id")
            )
            if continue_conv:
                wf_id = st.session_state.conversation_workflow_id
                is_continuation = True
            else:
                wf_id = f"conv_{uuid.uuid4().hex[:8]}"
                st.session_state.conversation_workflow_id = wf_id
                is_continuation = False

            if is_continuation:
                st.session_state.execution_feed.append({
                    "role": "Human (Web)",
                    "content": user_prompt,
                    "streaming": False,
                })
                st.session_state.logs.append(f"─── Continue {wf_id} ───")
            else:
                st.session_state.execution_feed = []
                st.session_state.agent_outputs = {}
                st.session_state.logs.append(f"─── New conversation {wf_id} ───")
                st.session_state.execution_feed.append({
                    "role": "Human (Web)",
                    "content": user_prompt,
                    "streaming": False,
                })

            st.session_state.metrics = {"tokens": 0, "cost": 0.0}
            st.session_state.last_event_count = 0
            st.session_state._seen_agent_starts = set()
            st.session_state.active_agent = "Queued..."
            st.session_state._workflow_submitted = False

            try:
                httpx.delete(f"{BACKEND_URL}/events", timeout=5.0)
            except Exception:
                pass

            st.session_state.current_workflow_id = wf_id
            st.session_state.is_processing = True
            st.session_state._processing_started_at = time.time()
            st.session_state._session_started = True
            st.session_state._current_prompt = user_prompt
            queue_runtime_ui(prompt="")

            try:
                resp = httpx.post(
                    f"{BACKEND_URL}/workflows/run",
                    json={
                        "template_id": st.session_state.selected_template_id,
                        "workflow_id": wf_id,
                        "user_prompt": user_prompt,
                    },
                    timeout=30.0,
                )
                resp.raise_for_status()
                st.session_state.logs.append(
                    f"🚀 Workflow {wf_id} queued — polling live events..."
                )
            except Exception as e:
                st.error(f"❌ Dispatch failed — is the backend running on port 8000? {e}")
                st.session_state.is_processing = False
                st.session_state.active_agent  = "Idle"
                st.session_state._session_started = False
            st.rerun()

    feed = st.session_state.get("execution_feed", [])
    if feed or is_proc or conv_id:
        st.markdown("---")
        _render_conversation_thread(feed, is_proc, conv_id)

    # Auto-save completed run to session history
    feed = st.session_state.get("execution_feed", [])
    agent_outputs = st.session_state.get("agent_outputs", {})
    if not st.session_state.is_processing and feed and st.session_state.get("_current_prompt"):
        history = st.session_state.workflow_history
        wf_id = st.session_state.get("current_workflow_id", "")
        if not history or history[0].get("id") != wf_id:
            history.insert(0, {
                "id": wf_id,
                "prompt": st.session_state._current_prompt,
                "feed": list(feed),
                "outputs": dict(agent_outputs),
                "tokens": st.session_state.metrics.get("tokens", 0),
                "cost": st.session_state.metrics.get("cost", 0.0),
            })
            st.session_state.workflow_history = history[:20]
        st.session_state._current_prompt = None

    if (
        not st.session_state.get("execution_feed")
        and not st.session_state.is_processing
        and not st.session_state.get("conversation_workflow_id")
    ):
        st.markdown(
            '<div style="text-align:center;padding:24px;color:#555;font-size:14px;">'
            '🛰️ No messages yet — send a message below to start</div>',
            unsafe_allow_html=True,
        )


# ── Session history panel ──────────────────────────────────────────────────────
def _render_history_panel():
    history = st.session_state.get("workflow_history", [])
    if not history:
        st.markdown(
            '<div style="text-align:center;padding:48px 24px;color:#555;font-size:14px;">'
            '📜 No completed workflows yet — run something first</div>',
            unsafe_allow_html=True
        )
        return

    st.markdown(f"**{len(history)} run{'s' if len(history) != 1 else ''} this session**")
    st.markdown("")
    for i, run in enumerate(history):
        preview = run["prompt"][:80] + ("..." if len(run["prompt"]) > 80 else "")
        with st.expander(f"**{run['id']}** — {preview}", expanded=(i == 0)):
            c1, c2, c3 = st.columns(3)
            c1.metric("Tokens", f"{run['tokens']:,}")
            c2.metric("Cost", f"${run['cost']:.6f}")
            c3.metric("Agents", len(run["outputs"]))
            st.markdown("**Prompt**")
            st.code(run["prompt"], language=None)
            if run["feed"]:
                st.markdown("**Execution feed**")
                _render_feed(run["feed"], newest_first=True)
            if run["outputs"]:
                st.markdown("**Agent outputs**")
                _render_snapshots(run["outputs"])


# ── Persisted DB messages panel ────────────────────────────────────────────────
def _render_db_messages_panel():
    st.markdown("#### 🗄️ Persisted Inter-Agent Messages")
    st.caption("Messages saved to the database across all sessions.")
    st.markdown("")

    # ── Only fetch when user explicitly clicks Load / Refresh ─────────────────
    # This prevents hammering GET /messages on every Streamlit rerun.
    if "db_messages_cache" not in st.session_state:
        st.session_state.db_messages_cache = None   # None = not loaded yet
    if "db_workflows_cache" not in st.session_state:
        st.session_state.db_workflows_cache = []

    col_load, col_filter, col_refresh = st.columns([1, 3, 1])

    with col_load:
        load_clicked = st.button("📂 Load", use_container_width=True)
    with col_refresh:
        refresh_clicked = st.button("🔄 Refresh", use_container_width=True,
                                    disabled=st.session_state.db_messages_cache is None)

    if load_clicked or refresh_clicked:
        try:
            wf_resp = httpx.get(f"{BACKEND_URL}/messages/workflows", timeout=3.0)
            st.session_state.db_workflows_cache = (
                wf_resp.json() if wf_resp.status_code == 200 else []
            )
            params = {"limit": 200}
            msg_resp = httpx.get(f"{BACKEND_URL}/messages", params=params, timeout=3.0)
            st.session_state.db_messages_cache = (
                msg_resp.json() if msg_resp.status_code == 200 else []
            )
        except Exception as e:
            st.error(f"⚠️ Could not reach backend: {e}")
            return

    # ── Nothing loaded yet ─────────────────────────────────────────────────────
    if st.session_state.db_messages_cache is None:
        st.info("Click **Load** to fetch persisted messages from the database.", icon="🗂️")
        return

    workflow_ids = st.session_state.db_workflows_cache
    messages     = st.session_state.db_messages_cache

    if not messages:
        st.info("No persisted messages found. Run a workflow first.", icon="🗂️")
        return

    # ── Filter selectbox (client-side, no HTTP call) ───────────────────────────
    with col_filter:
        selected_wf = st.selectbox(
            "Filter by workflow",
            options=["All workflows"] + workflow_ids,
            key="db_wf_filter"
        )

    if selected_wf != "All workflows":
        messages = [m for m in messages if m["workflow_id"] == selected_wf]

    if not messages:
        st.info("No messages match this filter.", icon="🔍")
        return

    st.markdown(f"**{len(messages)} message{'s' if len(messages) != 1 else ''}**")
    st.markdown("")

    from collections import defaultdict
    grouped = defaultdict(list)
    for msg in messages:
        grouped[msg["workflow_id"]].append(msg)

    for wf_id, msgs in grouped.items():
        msgs_sorted = sorted(msgs, key=lambda m: m.get("id", 0))
        with st.expander(
            f"Workflow `{wf_id}` — {len(msgs_sorted)} message{'s' if len(msgs_sorted) != 1 else ''}",
            expanded=True,
        ):
            for msg in msgs_sorted:
                sender = msg.get("sender", "unknown")
                content = msg.get("content", "")
                _, header = _card_class(sender)
                css, _ = _card_class(sender)
                st.markdown(
                    f'<div class="msg-row" style="border-color:#333;">'
                    f'<div class="msg-sender">{header}</div>'
                    f'<div class="feed-card {css}" style="margin:0;border:none;padding:8px 0;background:transparent;">'
                    f'{_safe(content[:2000])}{"..." if len(content) > 2000 else ""}'
                    f'</div></div>',
                    unsafe_allow_html=True,
                )