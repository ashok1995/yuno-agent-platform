"""Tests for Ollama engine helpers."""
from app.engine import build_ollama_payload, extract_stream_token
from app.config import settings


def test_build_ollama_payload_disables_thinking():
    payload = build_ollama_payload("qwen3.5:9b", "hello")
    assert payload["think"] is False
    assert payload["model"] == "qwen3.5:9b"


def test_extract_stream_token_prefers_response():
    token, kind = extract_stream_token({"response": "Paris", "thinking": "hmm"})
    assert token == "Paris"
    assert kind == "response"


def test_extract_stream_token_falls_back_to_thinking():
    token, kind = extract_stream_token({"response": "", "thinking": "Thinking..."})
    assert token == "Thinking..."
    assert kind == "thinking"
