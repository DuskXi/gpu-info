import asyncio
import logging
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
import os
from fastapi.middleware.cors import CORSMiddleware
from monitor import monitor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

active_connections = set()
history_buffer = deque(maxlen=120)
latest_data = {}

hw_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="hw")
POLL_TIMEOUT = 8.0


async def safe_call(loop: asyncio.AbstractEventLoop, fn, timeout: float = POLL_TIMEOUT):
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(hw_executor, fn),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.warning("Polling timeout: %s", fn.__name__)
        return None
    except Exception as e:
        logger.warning("Polling error for %s: %s", fn.__name__, e)
        return None


async def poll_hardware():
    global latest_data
    loop = asyncio.get_running_loop()
    while True:
        try:
            (
                sys_info,
                gpu_info,
                proc_info,
                proc_util,
                cuda_info,
            ) = await asyncio.gather(
                safe_call(loop, monitor.get_system_info),
                safe_call(loop, monitor.get_gpu_info),
                safe_call(loop, monitor.get_process_info),
                safe_call(loop, monitor.get_process_utilization),
                safe_call(loop, monitor.get_cuda_info),
            )

            prev = latest_data or {}
            data = {
                "timestamp": time.time(),
                "system": sys_info if sys_info is not None else prev.get("system"),
                "gpus": gpu_info if gpu_info is not None else prev.get("gpus", []),
                "processes": (
                    proc_info if proc_info is not None else prev.get("processes", [])
                ),
                "process_utilization": (
                    proc_util
                    if proc_util is not None
                    else prev.get("process_utilization", [])
                ),
                "cuda": cuda_info if cuda_info is not None else prev.get("cuda"),
            }

            latest_data = data
            history_buffer.append(data)

            dead_conns = set()
            for ws in active_connections:
                try:
                    await ws.send_json(data)
                except Exception:
                    dead_conns.add(ws)
            active_connections.difference_update(dead_conns)

        except Exception as e:
            logger.error("Hardware polling error: %s", e)

        await asyncio.sleep(1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    poll_task = asyncio.create_task(poll_hardware())
    yield
    poll_task.cancel()
    try:
        await poll_task
    except asyncio.CancelledError:
        pass
    hw_executor.shutdown(wait=False, cancel_futures=True)


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/history")
async def get_history():
    return list(history_buffer)


@app.get("/api/status")
async def status():
    return latest_data


@app.websocket("/api/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_connections.add(websocket)
    try:
        if latest_data:
            await websocket.send_json(latest_data)
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        active_connections.discard(websocket)


if os.path.exists("frontend/dist"):
    app.mount("/", StaticFiles(directory="frontend/dist", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
