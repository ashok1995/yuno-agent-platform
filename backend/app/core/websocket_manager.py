import json
from typing import List, Dict
from fastapi import WebSocket

class ConnectionManager:
    def __init__(self):
        # Keeps track of all active browser/Streamlit connections
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message_type: str, data: Dict):
        """
        Sends real-time updates (like tokens or state changes) 
        to all connected Streamlit frontends.
        """
        payload = {
            "type": message_type,
            "data": data
        }
        message_str = json.dumps(payload)
        
        # Iterate over a copy of the list to prevent modification errors during loop
        for connection in list(self.active_connections):
            try:
                await connection.send_text(message_str)
            except Exception:
                # If a tab was closed abruptly, remove it safely
                self.disconnect(connection)

# Global connection manager instance
ws_manager = ConnectionManager()