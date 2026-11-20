"""Access control shared by every deployment: per-IP access log, per-IP request rate limit,
and (for the read-only build) a hard lock that leaves only safe, state-free HTTP methods.

The middleware is pure ASGI so it also sees WebSocket handshakes. Client addresses come from
the TCP peer only; X-Forwarded-For is ignored because any LAN client could forge it.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRoute

import build_profile

logger = logging.getLogger("gpu_info.access")

SAFE_METHODS = frozenset({"GET", "HEAD"})


def _int_env(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, str(default))))
    except ValueError:
        return default


class RateLimiter:
    """Sliding 60 s window per client IP. limit=0 disables limiting."""

    def __init__(self, limit_per_minute: int, max_clients: int = 4096) -> None:
        self.limit = limit_per_minute
        self.max_clients = max_clients
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, client: str, now: float | None = None) -> tuple[bool, int]:
        """Return (allowed, retry_after_seconds)."""
        if self.limit <= 0:
            return True, 0
        now = time.monotonic() if now is None else now
        with self._lock:
            window = self._hits.get(client)
            if window is None:
                if len(self._hits) >= self.max_clients:
                    self._evict(now)
                window = self._hits[client] = deque()
            while window and now - window[0] >= 60:
                window.popleft()
            if len(window) >= self.limit:
                return False, max(1, int(60 - (now - window[0])) + 1)
            window.append(now)
            return True, 0

    def _evict(self, now: float) -> None:
        for key in [k for k, v in self._hits.items() if not v or now - v[-1] >= 60]:
            del self._hits[key]
        if len(self._hits) >= self.max_clients:  # still full: drop the oldest client
            del self._hits[min(self._hits, key=lambda k: self._hits[k][-1])]


class AccessGuard:
    """ASGI middleware: method lock (read-only build), rate limit, access log."""

    def __init__(self, app: Any, *, readonly: bool, limiter: RateLimiter,
                 max_websockets_per_ip: int, log_path: str | None) -> None:
        self.app = app
        self.readonly = readonly
        self.limiter = limiter
        self.max_websockets_per_ip = max_websockets_per_ip
        self.log_path = log_path
        self._sockets: dict[str, int] = {}
        self._sockets_lock = threading.Lock()
        self._log_lock = threading.Lock()

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        kind = scope["type"]
        if kind not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        client = (scope.get("client") or ("unknown", 0))[0]
        method = scope.get("method", "WS") if kind == "http" else "WS"
        path = scope.get("path", "")
        started = time.monotonic()
        status: dict[str, Any] = {"code": None, "note": None}

        def finish() -> None:
            self._log(client, method, path, scope, status, started)

        if self.readonly and kind == "http" and method not in SAFE_METHODS:
            status.update(code=405, note="readonly")
            await _reject_http(send, 405, "只读部署：不允许修改操作", {"allow": "GET, HEAD"})
            finish()
            return
        allowed, retry_after = self.limiter.allow(client)
        if not allowed:
            status.update(code=429, note="rate_limited")
            if kind == "http":
                await _reject_http(send, 429, "请求过于频繁", {"retry-after": str(retry_after)})
            else:
                await send({"type": "websocket.close", "code": 1008})
            finish()
            return

        if kind == "websocket":
            if not self._acquire_socket(client):
                status.update(code=1008, note="too_many_websockets")
                await send({"type": "websocket.close", "code": 1008})
                finish()
                return
            status.update(code=101)
            self._log(client, method, path, scope, {"code": 101, "note": "open"}, started)
            try:
                await self.app(scope, receive, send)
            finally:
                self._release_socket(client)
                status["note"] = "closed"
                finish()
            return

        async def send_wrapper(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            finish()

    def _acquire_socket(self, client: str) -> bool:
        if self.max_websockets_per_ip <= 0:
            return True
        with self._sockets_lock:
            if self._sockets.get(client, 0) >= self.max_websockets_per_ip:
                return False
            self._sockets[client] = self._sockets.get(client, 0) + 1
            return True

    def _release_socket(self, client: str) -> None:
        if self.max_websockets_per_ip <= 0:
            return
        with self._sockets_lock:
            remaining = self._sockets.get(client, 1) - 1
            if remaining > 0:
                self._sockets[client] = remaining
            else:
                self._sockets.pop(client, None)

    def _log(self, client: str, method: str, path: str, scope: dict[str, Any],
             status: dict[str, Any], started: float) -> None:
        headers = dict(scope.get("headers") or [])
        record = {
            "ts": round(time.time(), 3), "ip": client, "method": method, "path": path,
            "query": scope.get("query_string", b"").decode("latin-1")[:256],
            "status": status["code"], "ms": round((time.monotonic() - started) * 1000, 1),
            "ua": headers.get(b"user-agent", b"").decode("latin-1")[:200],
        }
        if status.get("note"):
            record["note"] = status["note"]
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        logger.info(line)
        if self.log_path:
            try:
                with self._log_lock, open(self.log_path, "a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            except OSError as exc:
                logger.warning("access log write failed: %s", exc)


async def _reject_http(send: Any, code: int, detail: str, extra: dict[str, str]) -> None:
    body = json.dumps({"detail": detail}, ensure_ascii=False).encode()
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
    headers += [(k.encode(), v.encode()) for k, v in extra.items()]
    await send({"type": "http.response.start", "status": code, "headers": headers})
    await send({"type": "http.response.body", "body": body})


def lock_routes_readonly(app: FastAPI) -> None:
    """Second line of defence: drop every route method that is not GET/HEAD, so even if the
    middleware were bypassed there is no handler that can change sampling state."""
    kept = []
    for route in app.router.routes:
        if isinstance(route, APIRoute):
            methods = set(route.methods or ()) & SAFE_METHODS
            if not methods:
                continue
            route.methods = methods
        kept.append(route)
    app.router.routes[:] = kept


def install(app: FastAPI) -> None:
    readonly = build_profile.READONLY
    if readonly:
        lock_routes_readonly(app)
    log_path = os.getenv("GPU_INFO_ACCESS_LOG") or None
    if log_path:
        os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
    logging.getLogger("gpu_info.access").setLevel(logging.INFO)
    if not logging.getLogger("gpu_info.access").handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("ACCESS %(message)s"))
        logging.getLogger("gpu_info.access").addHandler(handler)
        logging.getLogger("gpu_info.access").propagate = False
    app.add_middleware(
        AccessGuard, readonly=readonly,
        limiter=RateLimiter(_int_env("GPU_INFO_RPM", 240 if readonly else 0)),
        max_websockets_per_ip=_int_env("GPU_INFO_MAX_WS_PER_IP", 8 if readonly else 0),
        log_path=log_path,
    )
