"""Shared Streamlit session-state helpers (avoid widget key conflicts)."""
import streamlit as st


def apply_pending_runtime_ui() -> None:
    """
    Apply queued template/prompt changes BEFORE widgets with keys
    `selected_template_id` / `prompt_input_raw` are rendered.
    """
    if "_pending_template_id" in st.session_state:
        st.session_state.selected_template_id = st.session_state.pop("_pending_template_id")

    if "_pending_prompt" in st.session_state:
        st.session_state.prompt_input_raw = st.session_state.pop("_pending_prompt")


def queue_runtime_ui(
    *,
    template_id: str | None = None,
    prompt: str | None = None,
) -> None:
    """Queue UI updates for the next rerun (safe after widgets exist)."""
    if template_id is not None:
        st.session_state._pending_template_id = template_id
    if prompt is not None:
        st.session_state._pending_prompt = prompt
