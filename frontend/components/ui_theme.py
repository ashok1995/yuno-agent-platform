"""Global UI theme and shared styles for the Streamlit dashboard."""
import streamlit as st


def inject_global_theme():
    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

    .stApp {
        background: linear-gradient(165deg, #0a0a0f 0%, #12121a 40%, #0d1117 100%);
    }

    .yuno-hero {
        background: linear-gradient(135deg, rgba(99,102,241,0.15) 0%, rgba(168,85,247,0.08) 50%, rgba(34,197,94,0.06) 100%);
        border: 1px solid rgba(99,102,241,0.25);
        border-radius: 16px;
        padding: 1.5rem 2rem;
        margin-bottom: 1.5rem;
    }
    .yuno-hero h1 {
        font-family: 'Inter', sans-serif;
        font-size: 1.75rem;
        font-weight: 700;
        background: linear-gradient(90deg, #a5b4fc, #c4b5fd, #86efac);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin: 0 0 0.25rem 0;
    }
    .yuno-hero p {
        color: #94a3b8;
        font-size: 0.9rem;
        margin: 0;
    }

    .metric-card {
        background: rgba(30,30,46,0.8);
        border: 1px solid rgba(71,85,105,0.4);
        border-radius: 12px;
        padding: 1rem 1.25rem;
        text-align: center;
    }
    .metric-card .label {
        font-size: 0.7rem;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        color: #64748b;
        margin-bottom: 0.35rem;
    }
    .metric-card .value {
        font-family: 'JetBrains Mono', monospace;
        font-size: 1.1rem;
        font-weight: 600;
        color: #e2e8f0;
    }
    .metric-card.active .value { color: #86efac; }

    .live-stream-box {
        background: #0d0d14;
        border: 1px solid #2d2d3a;
        border-radius: 14px;
        padding: 1.25rem;
        min-height: 320px;
        max-height: 520px;
        overflow-y: auto;
        font-family: 'JetBrains Mono', monospace;
        font-size: 0.85rem;
        line-height: 1.65;
        color: #cbd5e1;
    }
    .live-stream-box.streaming {
        border-color: rgba(34,197,94,0.5);
        box-shadow: 0 0 24px rgba(34,197,94,0.08);
    }
    .stream-agent-label {
        font-size: 0.65rem;
        font-weight: 600;
        letter-spacing: 0.1em;
        text-transform: uppercase;
        color: #a78bfa;
        margin-bottom: 0.5rem;
    }
    .stream-cursor {
        display: inline-block;
        width: 8px;
        height: 1em;
        background: #86efac;
        margin-left: 2px;
        animation: blink 0.9s step-end infinite;
        vertical-align: text-bottom;
    }
    @keyframes blink { 50% { opacity: 0; } }

    .status-pill {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        padding: 4px 12px;
        border-radius: 999px;
        font-size: 0.75rem;
        font-weight: 600;
    }
    .status-pill.live {
        background: rgba(34,197,94,0.15);
        color: #86efac;
        border: 1px solid rgba(34,197,94,0.4);
    }
    .status-pill.idle {
        background: rgba(100,116,139,0.15);
        color: #94a3b8;
        border: 1px solid rgba(100,116,139,0.3);
    }

    .tool-badge {
        display: inline-block;
        padding: 3px 10px;
        border-radius: 6px;
        font-size: 0.7rem;
        font-weight: 600;
        margin: 2px 4px 2px 0;
        background: rgba(251,191,36,0.12);
        color: #fcd34d;
        border: 1px solid rgba(251,191,36,0.3);
    }

    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #0f0f16 0%, #14141f 100%);
        border-right: 1px solid rgba(71,85,105,0.3);
        min-width: 280px !important;
    }
    [data-testid="stSidebar"][aria-expanded="false"] {
        margin-left: 0;
    }
    button[data-testid="stSidebarCollapseButton"],
    button[data-testid="baseButton-headerNoPadding"] {
        color: #94a3b8;
    }

    div[data-testid="stMetricValue"] {
        font-family: 'JetBrains Mono', monospace;
    }
    </style>
    """, unsafe_allow_html=True)
