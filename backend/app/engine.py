import json
import logging
import httpx
from app.config import settings
from app.event_store import push_event

logger = logging.getLogger("yuno.engine")


class ConnectionManager:
    def __init__(self):
        self.active_connections = []

    async def connect(self, websocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        push_event(message)
        for connection in self.active_connections:
            try:
                await connection.send_text(json.dumps(message))
            except Exception:
                pass


ws_manager = ConnectionManager()


def build_ollama_payload(model: str, prompt: str, stream: bool = True) -> dict:
    """
    Build Ollama /api/generate payload.
    think=false disables Qwen3.5 'thinking' mode so tokens stream in `response`
    instead of being hidden in `thinking` (which made the UI look stuck).
    """
    return {
        "model": model,
        "prompt": prompt,
        "stream": stream,
        "think": settings.OLLAMA_THINK,
    }


def extract_stream_token(chunk_data: dict) -> tuple[str, str]:
    """
    Returns (token_text, stream_kind) where stream_kind is 'response' or 'thinking'.
    Prefers response; falls back to thinking so UI is never blank during inference.
    """
    response = chunk_data.get("response") or ""
    if response:
        return response, "response"
    thinking = chunk_data.get("thinking") or ""
    if thinking:
        return thinking, "thinking"
    return "", "response"


async def stream_ollama(model: str, prompt: str, agent_name: str, workflow_id: str) -> str:
    """Stream tokens from Ollama and broadcast TOKEN_STREAM events."""
    await ws_manager.broadcast({
        "type": "AGENT_START",
        "data": {"agent_name": agent_name, "workflow_id": workflow_id},
    })

    accumulated = ""
    payload = build_ollama_payload(model, prompt, stream=True)

    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream("POST", settings.OLLAMA_URL, json=payload) as response:
                if response.status_code != 200:
                    err = f"\n[Ollama HTTP {response.status_code}]"
                    accumulated += err
                    await ws_manager.broadcast({
                        "type": "TOKEN_STREAM",
                        "data": {"token": err, "agent_name": agent_name, "metrics": None},
                    })
                    return accumulated

                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    token, kind = extract_stream_token(data)
                    if not token:
                        if data.get("done"):
                            break
                        continue

                    accumulated += token
                    input_tokens = len(prompt.split()) * 1.3
                    output_tokens = len(accumulated.split()) * 1.3
                    total_tokens = int(input_tokens + output_tokens)
                    estimated_cost = round((total_tokens / 1000) * 0.00015, 6)

                    await ws_manager.broadcast({
                        "type": "TOKEN_STREAM",
                        "data": {
                            "token": token,
                            "agent_name": agent_name,
                            "stream_kind": kind,
                            "metrics": {
                                "total_tokens": total_tokens,
                                "cost": estimated_cost,
                            },
                        },
                    })

    except Exception as e:
        logger.error(f"Ollama stream error for {agent_name}: {e}")
        err_token = f"\n[Error: {e}]"
        accumulated += err_token
        await ws_manager.broadcast({
            "type": "TOKEN_STREAM",
            "data": {"token": err_token, "agent_name": agent_name, "metrics": None},
        })

    return accumulated


async def invoke_local_agent(agent_name: str, model: str, prompt: str, workflow_id: str) -> str:
    """Invokes local Ollama and streams tokens to the event store."""
    return await stream_ollama(model, prompt, agent_name, workflow_id)


async def call_ollama_collect(model: str, prompt: str) -> str:
    """Non-streaming Ollama call for routing JSON (think=false for speed)."""
    payload = build_ollama_payload(model, prompt, stream=False)
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(settings.OLLAMA_URL, json=payload)
        if resp.status_code != 200:
            raise RuntimeError(f"Ollama HTTP {resp.status_code}")
        data = resp.json()
        text = data.get("response") or data.get("thinking") or ""
        return text.strip()
