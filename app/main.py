"""app/main.py — FastAPI application entry point."""

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from app.config import config
from app.encoder import current_signal, encode_loop
from app.fm_receiver import read_fm

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# WebSocket connection manager
# ---------------------------------------------------------------------------

class ConnectionManager:
    def __init__(self) -> None:
        self.active: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.active.add(ws)
        logger.info("WebSocket connected; total=%d", len(self.active))

    def disconnect(self, ws: WebSocket) -> None:
        self.active.discard(ws)
        logger.info("WebSocket disconnected; total=%d", len(self.active))

    async def broadcast_audio(self, data: bytes) -> None:
        if not self.active:
            return
        dead: set[WebSocket] = set()
        for ws in list(self.active):
            try:
                await ws.send_bytes(data)
            except Exception:
                dead.add(ws)
        for ws in dead:
            self.disconnect(ws)

    async def broadcast_signal(self, signal: dict) -> None:
        if not self.active:
            return
        try:
            payload = json.dumps({"type": "signal", **signal}, default=str)
        except Exception as exc:
            logger.warning("JSON serialisation error: %s", exc)
            return
        dead: set[WebSocket] = set()
        for ws in list(self.active):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.add(ws)
        for ws in dead:
            self.disconnect(ws)

    async def broadcast_config(self) -> None:
        """Push the current config to all clients (after a settings change)."""
        if not self.active:
            return
        try:
            payload = json.dumps({"type": "config", **config.to_dict()})
        except Exception:
            return
        dead: set[WebSocket] = set()
        for ws in list(self.active):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.add(ws)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()


# ---------------------------------------------------------------------------
# Application lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    queue: asyncio.Queue = asyncio.Queue(maxsize=50)
    t1 = asyncio.create_task(read_fm(queue), name="fm_reader")
    t2 = asyncio.create_task(
        encode_loop(queue, manager.broadcast_audio, manager.broadcast_signal),
        name="encoder",
    )
    logger.info("Background tasks started")
    try:
        yield
    finally:
        t1.cancel()
        t2.cancel()
        await asyncio.gather(t1, t2, return_exceptions=True)


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(lifespan=lifespan)


@app.get("/")
async def index():
    return FileResponse("static/index.html")


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/status")
async def status():
    return {
        "signal": current_signal,
        "config": config.to_dict(),
        "channels": config.MARINE_CHANNELS,
    }


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await manager.connect(ws)
    # Send current config immediately so the UI is populated on connect
    try:
        await ws.send_text(json.dumps({"type": "config", **config.to_dict()}))
    except Exception:
        pass
    try:
        while True:
            raw = await ws.receive_text()
            await _handle_command(raw)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.warning("WebSocket receive error: %s", exc)
    finally:
        manager.disconnect(ws)


async def _handle_command(raw: str) -> None:
    """Parse and apply a JSON command received from the browser."""
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Received non-JSON WebSocket message: %r", raw)
        return

    if msg.get("cmd") != "set":
        return

    updates = {k: v for k, v in msg.items() if k != "cmd"}
    if not updates:
        return

    needs_restart, _ = config.apply(updates)
    logger.info("Config updated: %s (restart=%s)", updates, needs_restart)

    # Broadcast the new config to all clients so every open tab stays in sync
    await manager.broadcast_config()
