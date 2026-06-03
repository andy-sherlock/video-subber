"""WebSocket connection registry — one job can have multiple viewers."""
from fastapi import WebSocket


class WebSocketManager:
    def __init__(self):
        self._connections: dict[str, list[WebSocket]] = {}

    async def register(self, job_id: str, ws: WebSocket):
        await ws.accept()
        self._connections.setdefault(job_id, []).append(ws)

    def unregister(self, job_id: str, ws: WebSocket):
        if job_id in self._connections:
            self._connections[job_id] = [
                c for c in self._connections[job_id] if c != ws
            ]

    async def send(self, job_id: str, message: dict):
        dead = []
        for ws in self._connections.get(job_id, []):
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.unregister(job_id, ws)


ws_manager = WebSocketManager()
