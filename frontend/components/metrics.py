import streamlit as st


def render_global_metrics():
    active = st.session_state.get("active_agent", "Idle")
    tokens = st.session_state.get("metrics", {}).get("tokens", 0)
    cost = st.session_state.get("metrics", {}).get("cost", 0.0)
    processing = st.session_state.get("is_processing", False)

    st.markdown(
        f"""
        <div class="yuno-hero">
            <h1>Yuno Agent Orchestration Platform</h1>
            <p>Multi-agent runtime · Real-time streaming · Local Ollama inference</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    m_col1, m_col2, m_col3, m_col4 = st.columns(4)
    active_cls = "metric-card active" if processing else "metric-card"

    with m_col1:
        st.markdown(
            f'<div class="{active_cls}"><div class="label">Active Agent</div>'
            f'<div class="value">{active}</div></div>',
            unsafe_allow_html=True,
        )
    with m_col2:
        st.markdown(
            f'<div class="metric-card"><div class="label">Tokens streamed</div>'
            f'<div class="value">{tokens:,}</div></div>',
            unsafe_allow_html=True,
        )
    with m_col3:
        st.markdown(
            f'<div class="metric-card"><div class="label">Est. cost</div>'
            f'<div class="value">${cost:.6f}</div></div>',
            unsafe_allow_html=True,
        )
    with m_col4:
        status = "Running" if processing else "Ready"
        color = "#86efac" if processing else "#94a3b8"
        st.markdown(
            f'<div class="metric-card"><div class="label">Runtime</div>'
            f'<div class="value" style="color:{color}">{status}</div></div>',
            unsafe_allow_html=True,
        )

    st.markdown(
        '<div style="margin-top:0.75rem;display:flex;flex-wrap:wrap;gap:6px;">'
        '<span class="tool-badge">◈ Orchestrator Router</span>'
        '<span class="tool-badge">◉ Reasoning Specialist</span>'
        '<span class="tool-badge" style="background:rgba(239,68,68,0.12);color:#f87171;border-color:rgba(239,68,68,0.3);">'
        '🔍 Code Review Specialist</span>'
        '</div>',
        unsafe_allow_html=True,
    )
