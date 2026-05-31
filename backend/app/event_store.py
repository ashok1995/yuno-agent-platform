"""
event_store.py — In-memory global event buffer.

The Streamlit frontend polls GET /events on every rerun to drain this buffer.
The backend (engine.py broadcast) writes to it on every agent event.
"""
from collections import deque
from typing import List

# Stores up to 1000 events; oldest are dropped automatically beyond that
_global_buffer: deque = deque(maxlen=1000)


def push_event(event: dict) -> None:
    """Append a new event to the global buffer."""
    _global_buffer.append(event)


def get_events() -> List[dict]:
    """Return all buffered events as a list (oldest first)."""
    return list(_global_buffer)


def clear_events() -> None:
    """Clear all buffered events. Called by the frontend after WORKFLOW_COMPLETE."""
    _global_buffer.clear()