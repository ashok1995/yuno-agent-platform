import httpx
import json
import asyncio
from app.core.websocket_manager import ws_manager

OLLAMA_API_URL = "http://localhost:11434/api/generate"

async def invoke_local_agent(agent_name: str, model: str, prompt: str, workflow_id: str):
    """
    Calls your local Ollama model asynchronously, capturing streaming tokens 
    and feeding them directly into the WebSocket connection.
    """
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": True
    }
    
    full_response = []
    
    # Notify the UI that this specific agent has started processing
    await ws_manager.broadcast("AGENT_START", {
        "agent_name": agent_name, 
        "workflow_id": workflow_id,
        "status": "Thinking..."
    })

    # Use HTTPX to non-blockingly stream chunks from your local Ollama engine
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            async with client.stream("POST", OLLAMA_API_URL, json=payload) as response:
                async for chunk in response.aiter_text():
                    if not chunk.strip():
                        continue
                    
                    # Ollama sends chunks separated by newlines
                    lines = chunk.split("\n")
                    for line in lines:
                        if line.strip():
                            try:
                                data = json.loads(line)
                                token = data.get("response", "")
                                full_response.append(token)
                                
                                # Broadcast individual tokens immediately to the UI
                                await ws_manager.broadcast("TOKEN_STREAM", {
                                    "agent_name": agent_name,
                                    "workflow_id": workflow_id,
                                    "token": token
                                })
                                
                                # If Ollama indicates it is finished, broadcast metrics
                                if data.get("done", False):
                                    eval_count = data.get("eval_count", 0)  # Total tokens generated
                                    eval_duration_ns = data.get("eval_duration", 1)  # Duration in nanoseconds
                                    
                                    # Calculate real-time speed on your M4 Pro hardware
                                    tokens_per_sec = eval_count / (eval_duration_ns / 1e9) if eval_duration_ns > 0 else 0
                                    
                                    await ws_manager.broadcast("AGENT_COMPLETE", {
                                        "agent_name": agent_name,
                                        "workflow_id": workflow_id,
                                        "full_text": "".join(full_response),
                                        "tokens_used": eval_count,
                                        "tokens_per_second": round(tokens_per_sec, 2)
                                    })
                            except json.JSONDecodeError:
                                continue
        except Exception as e:
            print(f"Error during agent execution: {e}")
            await ws_manager.broadcast("AGENT_COMPLETE", {
                "agent_name": agent_name,
                "workflow_id": workflow_id,
                "full_text": f"Error: {str(e)}",
                "tokens_used": 0,
                "tokens_per_second": 0.0
            })
                    
    return "".join(full_response)