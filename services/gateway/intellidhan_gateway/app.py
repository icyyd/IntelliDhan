"""FastAPI gateway — REST + WebSocket + static dashboard (doc 01 §2⑦).

Run: .venv/bin/uvicorn intellidhan_gateway.app:app --port 8321
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

from intellidhan_gateway.live import LiveLoop

WEB_DIR = Path(__file__).resolve().parents[3] / "web"

loop = LiveLoop()


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(loop.run_forever())
    yield
    task.cancel()


app = FastAPI(title="IntelliDhan", lifespan=lifespan)


@app.get("/api/health")
async def health():
    return {"ok": True, "started_at": loop.started_at, "last_poll": loop.last_poll}


@app.get("/api/state")
async def state():
    return JSONResponse(loop.snapshot())


@app.get("/api/calibration")
async def calibration():
    out = {}
    for f in sorted(Path("config/calibration").glob("*.json")):
        out[f.stem] = json.loads(f.read_text())
    return out


@app.get("/api/briefing")
async def briefing():
    return loop.last_briefing or {"status": "not generated yet (8:30 ET on trading days)"}


@app.get("/api/budgets")
async def get_budgets():
    return loop.composer.budgets.as_dict()


@app.put("/api/budgets")
async def put_budgets(new_budgets: dict = Body(...)):
    """Persist edited budgets to config/budgets.yaml; sizing picks them up on
    the very next alert — no restart (doc 09 §3, hot-reload)."""
    try:
        loop.composer.budgets.update(new_budgets)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return loop.composer.budgets.as_dict()


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    q: asyncio.Queue = asyncio.Queue()
    loop.ws_subscribers.append(q)
    try:
        while True:
            msg = await q.get()
            await websocket.send_json(msg)
    except WebSocketDisconnect:
        pass
    finally:
        loop.ws_subscribers.remove(q)


@app.get("/")
async def index():
    return FileResponse(WEB_DIR / "index.html")
