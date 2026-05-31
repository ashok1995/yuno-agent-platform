"""Sidebar runtime stats — tokens & cost only (streaming lives in center execution feed)."""
import streamlit as st


def render_sidebar_metrics(is_processing: bool):
    """Compact sidebar: live/idle status, active agent name, tokens, cost."""
    active = st.session_state.get("active_agent", "Idle")
    metrics = st.session_state.get("metrics", {"tokens": 0, "cost": 0.0})

    status_cls = "live" if is_processing else "idle"
    status_text = "● LIVE" if is_processing else "○ IDLE"

    st.markdown(
        f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.75rem;">'
        f'<span style="font-weight:600;color:#e2e8f0;">Runtime</span>'
        f'<span class="status-pill {status_cls}">{status_text}</span></div>',
        unsafe_allow_html=True,
    )

    if is_processing and active not in ("Idle", "Starting...", "Queued..."):
        st.caption(f"Active: **{active}**")
    else:
        st.caption("Stream output appears in the **Execution Feed** (center).")

    st.markdown(
        f'<div class="metric-card" style="margin-top:0.5rem;">'
        f'<div class="label">Tokens</div>'
        f'<div class="value">{metrics.get("tokens", 0):,}</div></div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<div class="metric-card" style="margin-top:0.5rem;">'
        f'<div class="label">Est. cost</div>'
        f'<div class="value">${metrics.get("cost", 0.0):.6f}</div></div>',
        unsafe_allow_html=True,
    )


def render_tool_badges():
    """Show available tools from backend."""
    import httpx
    try:
        resp = httpx.get("http://localhost:8000/tools", timeout=2.0)
        if resp.status_code != 200:
            return
        tools = resp.json().get("tools", [])
    except Exception:
        return

    if not tools:
        return

    st.markdown("**Agent tools**")
    badges = "".join(
        f'<span class="tool-badge" title="{t.get("description", "")}">'
        f'{t.get("icon", "⚙")} {t.get("name")}</span>'
        for t in tools
    )
    st.markdown(badges, unsafe_allow_html=True)
