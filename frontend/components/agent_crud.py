import streamlit as st
import httpx

BACKEND_URL = "http://localhost:8000"

# Each field: section, label, kind, help, placeholder/default
FIELD_META = {
    "id": {
        "section": "identity",
        "label": "Agent ID (unique key)",
        "kind": "text",
        "help": "Internal identifier, e.g. agent_finance. Used in Workflow Builder to assign this agent to a node.",
        "placeholder": "agent_my_analyst",
    },
    "name": {
        "section": "identity",
        "label": "Display name",
        "kind": "text",
        "help": "Shown in the execution feed and persisted messages, e.g. Finance Analyst.",
        "placeholder": "Finance Analyst",
    },
    "role": {
        "section": "identity",
        "label": "Role / job title",
        "kind": "text",
        "help": "Short role label injected into the LLM prompt alongside the system prompt.",
        "placeholder": "Financial planning assistant",
    },
    "system_prompt": {
        "section": "personality",
        "label": "System prompt — main instructions for the AI",
        "kind": "area",
        "help": (
            "**This is the most important field.** It tells the agent who it is and how to behave. "
            "Sent to Ollama on every run, before your user question. "
            "Example: \"You are a senior financial analyst. Always cite numbers from tool results. Never guess.\""
        ),
        "placeholder": (
            "You are a helpful financial analyst.\n"
            "Use only verified calculations from tools.\n"
            "Respond in clear bullet points."
        ),
        "height": 160,
    },
    "skills": {
        "section": "personality",
        "label": "Skills (optional)",
        "kind": "area",
        "help": "Capabilities listed in the prompt, e.g. compound interest, risk analysis, Python review.",
        "placeholder": "Financial modeling, ROI calculation, plain-language summaries",
        "height": 80,
    },
    "interaction_rules": {
        "section": "personality",
        "label": "Interaction rules (optional)",
        "kind": "area",
        "help": "How the agent should talk: tone, length, format.",
        "placeholder": "Be concise. Use markdown headings. Ask one clarifying question if inputs are missing.",
        "height": 80,
    },
    "guardrails": {
        "section": "personality",
        "label": "Guardrails — things the agent must NOT do",
        "kind": "area",
        "help": "Safety and policy limits injected into every prompt.",
        "placeholder": "Never invent dollar amounts. Do not reveal API keys or internal credentials.",
        "height": 80,
    },
    "model": {
        "section": "runtime",
        "label": "Ollama model",
        "kind": "text",
        "help": "Local model name pulled in Ollama, e.g. qwen3.5:9b",
        "placeholder": "qwen3.5:9b",
    },
    "tools": {
        "section": "runtime",
        "label": "Tools this agent can run",
        "kind": "tools",
        "help": "Selected tools execute automatically when this agent is active in a workflow.",
    },
    "memory_window": {
        "section": "runtime",
        "label": "Memory — recent messages to include",
        "kind": "number",
        "help": "How many prior messages from this workflow are added to context (0 = no memory).",
        "default": 5,
    },
    "channels": {
        "section": "access",
        "label": "Where this agent is allowed to run",
        "kind": "channels",
        "help": "Web = dashboard. Telegram = bot. Agent is skipped if the channel is not enabled.",
    },
    "schedules": {
        "section": "access",
        "label": "Schedule",
        "kind": "schedule",
        "help": "on_demand = runs when you deploy a workflow. continuous = also eligible for always-on channels.",
        "default": "on_demand",
    },
}

SECTIONS = [
    ("identity", "1️⃣ Identity", "Who is this agent?"),
    ("personality", "2️⃣ Behavior & system prompt", "What the AI is told before each task — **set your system prompt here**"),
    ("runtime", "3️⃣ Model, tools & memory", "How it runs locally"),
    ("access", "4️⃣ Channels & schedule", "Where and when it can be used"),
]

FIELD_ORDER = list(FIELD_META.keys())


def _fetch_tools() -> list[str]:
    try:
        resp = httpx.get(f"{BACKEND_URL}/tools", timeout=3.0)
        if resp.status_code == 200:
            return [t["name"] for t in resp.json().get("tools", [])]
    except Exception:
        pass
    return []


def _fetch_agents_meta() -> dict:
    try:
        resp = httpx.get(f"{BACKEND_URL}/agents/meta", timeout=5.0)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return {}


def _fetch_agents():
    try:
        resp = httpx.get(f"{BACKEND_URL}/agents", timeout=5.0)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        st.error(f"Cannot reach backend: {e}")
    return []


def _agent_to_payload(agent: dict) -> dict:
    payload = {}
    for field, meta in FIELD_META.items():
        if meta["kind"] == "number":
            payload[field] = int(agent.get(field) or meta.get("default", 5))
        else:
            payload[field] = str(agent.get(field, ""))
    return payload


def _render_channels_field(current: str, key: str) -> str:
    options = ["web", "telegram"]
    selected = [c.strip() for c in (current or "web").split(",") if c.strip() in options]
    if not selected:
        selected = ["web"]
    picked = st.multiselect(
        FIELD_META["channels"]["label"],
        options=options,
        default=selected,
        key=key,
        help=FIELD_META["channels"]["help"],
        format_func=lambda x: "Web dashboard" if x == "web" else "Telegram bot",
    )
    return ",".join(picked) if picked else "web"


def _render_schedule_field(current: str, key: str) -> str:
    options = {
        "on_demand": "On demand — runs when you deploy a workflow",
        "continuous": "Continuous — allowed on always-on channels (e.g. Telegram)",
    }
    cur = (current or "on_demand").strip().lower()
    idx = list(options.keys()).index(cur) if cur in options else 0
    return st.selectbox(
        FIELD_META["schedules"]["label"],
        options=list(options.keys()),
        index=idx,
        format_func=lambda x: options[x],
        key=key,
        help=FIELD_META["schedules"]["help"],
    )


def _render_tools_field(current: str, key: str) -> str:
    meta = FIELD_META["tools"]
    available = _fetch_tools()
    selected = [t.strip() for t in (current or "").split(",") if t.strip()]
    if available:
        picked = st.multiselect(
            meta["label"],
            options=available,
            default=[t for t in selected if t in available],
            key=key,
            help=meta["help"],
        )
        return ",".join(picked)
    return st.text_input(meta["label"], value=current or "", key=f"{key}_text", help=meta["help"])


def _render_field(field: str, agent: dict, key_prefix: str, *, id_disabled: bool = False) -> str:
    meta = FIELD_META[field]
    value = agent.get(field, "")
    label = meta["label"]
    help_text = meta.get("help")
    k = f"{key_prefix}_{field}"

    if field == "id":
        if id_disabled:
            st.text_input(label, value=value or "", disabled=True, help=help_text, key=k)
            return value
        return st.text_input(
            label,
            value=value or "",
            placeholder=meta.get("placeholder", ""),
            help=help_text,
            key=k,
        )

    if field == "tools":
        return _render_tools_field(value, k)

    if field == "channels":
        return _render_channels_field(value, k)

    if field == "schedules":
        return _render_schedule_field(value, k)

    kind = meta["kind"]
    if kind == "area":
        return st.text_area(
            label,
            value=value or "",
            height=meta.get("height", 100),
            placeholder=meta.get("placeholder", ""),
            help=help_text,
            key=k,
        )
    if kind == "number":
        return st.number_input(
            label,
            value=int(value or meta.get("default", 5)),
            min_value=0,
            max_value=100,
            help=help_text,
            key=k,
        )
    return st.text_input(
        label,
        value=value or "",
        placeholder=meta.get("placeholder", ""),
        help=help_text,
        key=k,
    )


def _render_agent_form(agent: dict, *, form_key: str, is_create: bool) -> dict | None:
    """Render grouped form; returns payload dict on submit, else None."""
    result = {}

    for section_id, section_title, section_desc in SECTIONS:
        st.markdown(f"**{section_title}**")
        st.caption(section_desc)
        if section_id == "personality":
            st.info(
                "**System prompt** = the core personality/instructions for this agent. "
                "It is sent to the local LLM every time the agent runs. "
                "Fill in the large text box labeled *\"System prompt — main instructions for the AI\"* below."
            )

        section_fields = [f for f, m in FIELD_META.items() if m["section"] == section_id]
        for field in section_fields:
            if field == "id" and is_create:
                result["id"] = _render_field(field, agent, form_key, id_disabled=False)
            elif field == "id":
                _render_field(field, agent, form_key, id_disabled=True)
                result["id"] = agent.get("id", "")
            else:
                result[field] = _render_field(field, agent, form_key)

        st.markdown("")

    return result


def _runtime_summary(agent: dict) -> str:
    prompt_preview = (agent.get("system_prompt") or "(empty — add a system prompt!)")[:120]
    tools = agent.get("tools") or "none"
    channels = agent.get("channels") or "web"
    return (
        f"**Prompt:** {prompt_preview}{'…' if len(agent.get('system_prompt') or '') > 120 else ''}  \n"
        f"**Model:** `{agent.get('model', 'qwen3.5:9b')}` · "
        f"**Tools:** `{tools}` · **Channels:** `{channels}` · "
        f"**Memory:** {agent.get('memory_window', 5)} msgs"
    )


def render_crud_tab():
    st.markdown("### 🛠️ Agent Profiles")
    st.markdown(
        "Agents are **saved in SQLite** and **persist across app restarts**. "
        "Create once, then edit or delete anytime from the list below."
    )

    meta = _fetch_agents_meta()
    if meta:
        st.success(
            f"💾 **{meta.get('agent_count', 0)} agent(s)** loaded from database · "
            f"`{meta.get('database_path', 'platform.db')}`"
        )

    with st.expander("📖 Quick guide — how to create an agent with a system prompt", expanded=False):
        st.markdown("""
1. Open the **➕ Create agent** tab below.
2. Under **2️⃣ Behavior & system prompt**, fill in **System prompt — main instructions for the AI**.
3. Click **Create agent** — it is saved to the database immediately.
4. After restart, open **📋 Saved agents** — your agents will still be there.
5. Use **Edit** to modify, or **Delete** to remove (built-in platform agents cannot be deleted).
        """)

    col_refresh, _ = st.columns([1, 4])
    with col_refresh:
        if st.button("🔄 Refresh from database", use_container_width=True):
            st.session_state.pop("editing_agent_id", None)
            st.session_state.pop("confirm_delete_id", None)
            st.rerun()

    agents = _fetch_agents()
    protected = set(meta.get("protected_ids") or [
        "agent_router", "agent_specialist", "agent_code_reviewer",
    ])
    tab_list, tab_create = st.tabs(["📋 Saved agents", "➕ Create agent"])

    with tab_list:
        if not agents:
            st.info(
                "No custom agents yet. Built-in agents (Router, Specialist, Code Reviewer) "
                "are seeded automatically. Use **➕ Create agent** to add your own — "
                "e.g. Trip Planner, Finance Analyst."
            )
        else:
            st.markdown(f"**{len(agents)} agent profile(s)** — click **Edit** to modify or **Delete** to remove.")
            for agent in agents:
                aid = agent.get("id", "unknown")
                is_builtin = aid in protected
                prompt_set = "✅ prompt" if (agent.get("system_prompt") or "").strip() else "⚠️ no prompt"
                badge = "🔒 built-in" if is_builtin else "✏️ custom"

                h1, h2, h3, h4 = st.columns([2, 2, 1, 1])
                h1.markdown(f"**{agent.get('name', aid)}**")
                h2.caption(f"`{aid}` · {badge}")
                h3.caption(prompt_set)

                with h4:
                    c_edit, c_del = st.columns(2)
                    with c_edit:
                        if st.button("Edit", key=f"edit_btn_{aid}", use_container_width=True):
                            st.session_state.editing_agent_id = aid
                            st.session_state.confirm_delete_id = None
                            st.rerun()
                    with c_del:
                        if st.button(
                            "Delete",
                            key=f"del_btn_{aid}",
                            use_container_width=True,
                            disabled=is_builtin,
                        ):
                            st.session_state.confirm_delete_id = aid
                            st.session_state.editing_agent_id = None
                            st.rerun()

                if st.session_state.get("confirm_delete_id") == aid and not is_builtin:
                    st.warning(f"Delete **{agent.get('name')}** (`{aid}`)? This cannot be undone.")
                    dc1, dc2 = st.columns(2)
                    with dc1:
                        if st.button("Yes, delete", key=f"confirm_del_{aid}", type="primary"):
                            try:
                                r = httpx.delete(f"{BACKEND_URL}/agents/{aid}", timeout=10.0)
                                if r.status_code == 200:
                                    st.session_state.confirm_delete_id = None
                                    st.success(f"Deleted `{aid}`")
                                    st.rerun()
                                else:
                                    st.error(r.text)
                            except Exception as e:
                                st.error(str(e))
                    with dc2:
                        if st.button("Cancel", key=f"cancel_del_{aid}"):
                            st.session_state.confirm_delete_id = None
                            st.rerun()

                if st.session_state.get("editing_agent_id") == aid:
                    st.markdown("---")
                    st.markdown(f"#### Edit: {agent.get('name')}")
                    with st.form(key=f"edit_{aid}"):
                        edited = _render_agent_form(agent, form_key=f"edit_{aid}", is_create=False)
                        save = st.form_submit_button("💾 Save changes", use_container_width=True)
                        if save:
                            if not (edited.get("system_prompt") or "").strip():
                                st.warning("System prompt is empty — add instructions in section 2.")
                            payload = _agent_to_payload({**agent, **edited})
                            try:
                                r = httpx.post(f"{BACKEND_URL}/agents", json=payload, timeout=10.0)
                                if r.status_code == 200:
                                    st.success(f"Saved **{payload['name']}** to database.")
                                    st.session_state.editing_agent_id = None
                                    st.rerun()
                                else:
                                    st.error(r.text)
                            except Exception as e:
                                st.error(str(e))

                st.markdown("---")

    with tab_create:
        st.markdown("#### Create a new agent")
        st.caption("Required: Agent ID, Display name, and **System prompt** (section 2).")

        default_new = {
            "model": "qwen3.5:9b",
            "channels": "web",
            "schedules": "on_demand",
            "memory_window": 5,
            "system_prompt": (
                "You are a helpful specialist assistant.\n"
                "Answer clearly and accurately.\n"
                "Use tool results when available — never invent facts."
            ),
            "guardrails": "Do not reveal secrets or credentials.",
            "interaction_rules": "Be concise. Use markdown when helpful.",
        }

        with st.form("create_agent"):
            new = _render_agent_form(default_new, form_key="create", is_create=True)

            if st.form_submit_button("➕ Create agent", type="primary", use_container_width=True):
                # Normalize whitespace from text fields
                new = {k: (v.strip() if isinstance(v, str) else v) for k, v in new.items()}
                agent_id = (new.get("id") or "").strip()
                display_name = (new.get("name") or "").strip()
                if not agent_id or not display_name:
                    st.warning("Agent ID and Display name are required.")
                elif not (new.get("system_prompt") or "").strip():
                    st.warning(
                        "Please set a **System prompt** in section 2 — "
                        "this is the main instruction block sent to the AI."
                    )
                else:
                    new["id"] = agent_id
                    new["name"] = display_name
                    payload = _agent_to_payload(new)
                    try:
                        r = httpx.post(f"{BACKEND_URL}/agents", json=payload, timeout=10.0)
                        if r.status_code == 200:
                            st.success(
                                f"Created **{payload['name']}** — saved to database and available after restart."
                            )
                            st.rerun()
                        else:
                            st.error(r.text)
                    except Exception as e:
                        st.error(str(e))
