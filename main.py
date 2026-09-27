"""Telemetry transport: one store owns polling, history, HTTP and WebSocket flow."""

import asyncio
import logging
import os
import time
import uuid
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from monitor import monitor

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TelemetryFrame:
    stream_id: str
    sequence: int
    timestamp: float
    payload: dict[str, Any]

    def as_message(self) -> dict[str, Any]:
        return {
            "type": "snapshot",
            "stream_id": self.stream_id,
            "sequence": self.sequence,
            "timestamp": self.timestamp,
            **self.payload,
        }


class TelemetryStore:
    """The only owner of collected samples and outbound messages."""

    def __init__(self, capacity: int = 120) -> None:
        self.history: deque[TelemetryFrame] = deque(maxlen=capacity)
        self.clients: set[asyncio.Queue[TelemetryFrame]] = set()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="nvml")
        self._pending: Future[dict[str, Any]] | None = None
        self._sequence = 0
        self.stream_id = uuid.uuid4().hex

    @property
    def latest(self) -> TelemetryFrame | None:
        return self.history[-1] if self.history else None

    def recent(self, limit: int) -> list[dict[str, Any]]:
        return [frame.as_message() for frame in list(self.history)[-limit:]]

    async def poll(self) -> None:
        if self._pending is None:
            self._pending = self.executor.submit(monitor.capture)
        try:
            payload = await asyncio.wait_for(
                asyncio.shield(asyncio.wrap_future(self._pending)), timeout=8
            )
        except asyncio.TimeoutError:
            # Keep waiting on the same worker; never queue overlapping NVML calls.
            logger.warning("Hardware capture exceeded 8 seconds")
            return
        except Exception:
            logger.exception("Hardware capture failed")
            self._pending = None
            return
        self._pending = None
        self._sequence += 1
        frame = TelemetryFrame(self.stream_id, self._sequence, time.time(), payload)
        self.history.append(frame)
        for queue in tuple(self.clients):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(frame)

    async def run(self) -> None:
        while True:
            started = time.monotonic()
            await self.poll()
            await asyncio.sleep(max(0, 1 - (time.monotonic() - started)))

    def subscribe(self) -> asyncio.Queue[TelemetryFrame]:
        queue: asyncio.Queue[TelemetryFrame] = asyncio.Queue(maxsize=1)
        self.clients.add(queue)
        if self.latest is not None:
            queue.put_nowait(self.latest)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[TelemetryFrame]) -> None:
        self.clients.discard(queue)

    def close(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)
        if self._pending is None or self._pending.done():
            monitor.close()


store = TelemetryStore()


@asynccontextmanager
async def lifespan(_: FastAPI):
    poll_task = asyncio.create_task(store.run())
    try:
        yield
    finally:
        poll_task.cancel()
        try:
            await poll_task
        except asyncio.CancelledError:
            pass
        store.close()


app = FastAPI(lifespan=lifespan)


@app.get("/api/status")
async def status() -> dict[str, Any]:
    if store.latest is None:
        raise HTTPException(status_code=503, detail="Waiting for first sample")
    return store.latest.as_message()


@app.get("/api/history")
async def history(limit: int = Query(default=120, ge=1, le=120)) -> list[dict[str, Any]]:
    return store.recent(limit)


@app.websocket("/api/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    queue = store.subscribe()
    try:
        while True:
            frame = await queue.get()
            await websocket.send_json(frame.as_message())
    except (WebSocketDisconnect, OSError, RuntimeError):
        pass
    finally:
        store.unsubscribe(queue)


dist = Path(__file__).parent / "frontend" / "dist"
if dist.is_dir():
    app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.getenv("GPU_INFO_HOST", "127.0.0.1"), port=8001)
