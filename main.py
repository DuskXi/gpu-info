"""Telemetry transport: one store owns polling, history, HTTP and WebSocket flow."""

import asyncio
import json
import logging
import os
import time
import uuid
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from activity_probe import ActivityProbe, ProbeError
from monitor import monitor
from status_exports import render_prometheus, render_status_markdown, status_message

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

    def __init__(self, capacity: int = 120, event_path: Path | None = None) -> None:
        self.history: deque[TelemetryFrame] = deque(maxlen=capacity)
        self.clients: set[asyncio.Queue[TelemetryFrame]] = set()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="nvml")
        self._pending: Future[dict[str, Any]] | None = None
        self._sequence = 0
        self.stream_id = uuid.uuid4().hex
        self.activity_probe = ActivityProbe()
        self.latest_activity: dict[str, Any] | None = None
        self.latest_pipeline_activity: dict[str, Any] | None = None
        self.event_path = event_path or Path(__file__).parent / "data" / "display-events.jsonl"
        self.display_events: deque[dict[str, Any]] = deque(maxlen=200)
        self._last_display: dict[str, Any] | None = None
        self._load_display_events()

    def _load_display_events(self) -> None:
        if not self.event_path.exists():
            return
        try:
            with self.event_path.open(encoding="utf-8") as source:
                for line in source:
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(event, dict) and "current" in event:
                        self.display_events.append(event)
        except OSError as exc:
            logger.warning("Display event history unavailable: %s", exc)

    def _record_display(self, payload: dict[str, Any]) -> None:
        current = payload.get("display_state")
        if current is None or current == self._last_display:
            payload["display_events"] = list(self.display_events)[-6:]
            return
        event = {
            "timestamp": time.time(),
            "kind": "baseline" if self._last_display is None else "change",
            "previous": self._last_display,
            "current": current,
            "gpu_memory": [
                {"gpu_id": gpu["id"], "used": gpu.get("memory", {}).get("used"),
                 "total": gpu.get("memory", {}).get("total")}
                for gpu in payload.get("gpus", [])
            ],
            "processes_sampled_at": payload.get("processes_sampled_at"),
            "dwm": [
                {"gpu_id": row["gpu_id"], "pid": row["pid"],
                 "local_memory": row.get("local_memory"),
                 "claimed_memory": row.get("claimed_memory")}
                for row in payload.get("processes", []) if row.get("name", "").lower() == "dwm.exe"
            ],
        }
        self._last_display = current
        self.display_events.append(event)
        try:
            self.event_path.parent.mkdir(parents=True, exist_ok=True)
            with self.event_path.open("a", encoding="utf-8") as destination:
                destination.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
        except OSError as exc:
            logger.warning("Display event could not be saved: %s", exc)
        payload["display_events"] = list(self.display_events)[-6:]

    @property
    def latest(self) -> TelemetryFrame | None:
        return self.history[-1] if self.history else None

    def recent(self, limit: int) -> list[dict[str, Any]]:
        return [frame.as_message() for frame in list(self.history)[-limit:]]

    async def capture_activity(
        self, gpu_id: int, kind: str = "general", duration_seconds: int = 5
    ) -> dict[str, Any]:
        result = await asyncio.to_thread(
            self.activity_probe.capture, gpu_id, kind, duration_seconds
        )
        result["process_context"] = self._activity_process_context(result)
        if kind == "pipelines":
            self.latest_pipeline_activity = result
        else:
            self.latest_activity = result
        return result

    def _activity_process_context(self, result: dict[str, Any]) -> list[dict[str, Any]]:
        """Show concurrent process SM samples without assigning GPU counters to PIDs."""
        start = result.get("window_started_at")
        end = result.get("window_ended_at")
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            return []
        samples: dict[int, list[float]] = {}
        process_info: dict[int, dict[str, Any]] = {}
        for frame in self.history:
            if not start - 1 <= frame.timestamp <= end + 1:
                continue
            for row in frame.payload.get("process_utilization", []):
                if row.get("gpu_id") != result["gpu_id"] or row.get("sm_util") is None:
                    continue
                samples.setdefault(row["pid"], []).append(float(row["sm_util"]))
                process_info[row["pid"]] = row
            for row in frame.payload.get("processes", []):
                if row.get("gpu_id") == result["gpu_id"]:
                    process_info[row["pid"]] = {**process_info.get(row["pid"], {}), **row}
        context = []
        for pid, values in samples.items():
            if max(values) <= 0:
                continue
            info = process_info[pid]
            context.append({
                "pid": pid, "name": info.get("name", "Unknown"),
                "used_memory": info.get("used_memory"),
                "sm_mean_percent": round(sum(values) / len(values), 1),
                "sm_peak_percent": max(values), "samples": len(values),
            })
        context.sort(key=lambda row: row["sm_mean_percent"], reverse=True)
        return context[:5]

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
        self._record_display(payload)
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


def latest_frame() -> TelemetryFrame:
    frame = store.latest
    if frame is None:
        raise HTTPException(status_code=503, detail="Waiting for first sample")
    return frame


@app.get("/status.json")
@app.get("/api/status")
async def status(response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    message = status_message(latest_frame().as_message())
    message["activity_probe"] = getattr(store, "latest_activity", None)
    message["pipeline_probe"] = getattr(store, "latest_pipeline_activity", None)
    return message


@app.get("/status.md")
async def status_markdown() -> Response:
    frame = store.latest
    if frame is None:
        return Response("# GPU Monitor 状态\n\n等待首次采样。\n", status_code=503,
                        media_type="text/markdown", headers={"Cache-Control": "no-store"})
    return Response(render_status_markdown(frame.as_message()), media_type="text/markdown",
                    headers={"Cache-Control": "no-store"})


@app.get("/metrics")
async def prometheus_metrics(include_processes: bool = False) -> Response:
    frame = store.latest
    if frame is None:
        return Response(
            "# HELP gpu_info_up A telemetry frame is available from the collector.\n"
            "# TYPE gpu_info_up gauge\n"
            "gpu_info_up 0\n",
            status_code=503,
            media_type="text/plain; version=0.0.4",
            headers={"Cache-Control": "no-store"},
        )
    return Response(
        render_prometheus(frame.as_message(), include_processes=include_processes),
        media_type="text/plain; version=0.0.4",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/history")
async def history(limit: int = Query(default=120, ge=1, le=120)) -> list[dict[str, Any]]:
    return store.recent(limit)


@app.post("/api/activity-probe")
async def activity_probe(
    gpu: int = Query(ge=0),
    kind: Literal["general", "pipelines"] = "general",
    duration: int = Query(default=5, ge=1, le=5),
) -> dict[str, Any]:
    frame = latest_frame()
    if not any(row["id"] == gpu for row in frame.payload.get("gpus", [])):
        raise HTTPException(status_code=404, detail="GPU not found")
    try:
        return await store.capture_activity(gpu, kind, duration)
    except ProbeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/activity-probe")
async def latest_activity_probe(
    kind: Literal["general", "pipelines"] = "general"
) -> dict[str, Any]:
    result = store.latest_pipeline_activity if kind == "pipelines" else store.latest_activity
    if result is None:
        raise HTTPException(status_code=404, detail="No activity probe captured yet")
    return result


@app.get("/api/display-events")
async def display_events(limit: int = Query(default=50, ge=1, le=200)) -> list[dict[str, Any]]:
    return list(store.display_events)[-limit:]


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
