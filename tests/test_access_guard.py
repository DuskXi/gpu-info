import json

from fastapi import FastAPI, WebSocket
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import access_guard
from access_guard import AccessGuard, RateLimiter, lock_routes_readonly


def make_app(readonly, rpm=0, ws=0, log_path=None):
    app = FastAPI()
    state = {"changed": False}

    @app.get("/read")
    async def read():
        return {"ok": True}

    @app.post("/write")
    async def write():
        state["changed"] = True
        return {"ok": True}

    @app.api_route("/both", methods=["GET", "POST"])
    async def both():
        return {"ok": True}

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_json({"hello": 1})
        await websocket.close()

    if readonly:
        lock_routes_readonly(app)
    app.add_middleware(AccessGuard, readonly=readonly, limiter=RateLimiter(rpm),
                       max_websockets_per_ip=ws, log_path=log_path)
    return app, state


def test_readonly_blocks_every_mutating_method():
    app, state = make_app(readonly=True)
    client = TestClient(app)
    assert client.get("/read").status_code == 200
    assert client.get("/both").status_code == 200
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        for path in ("/write", "/both", "/read", "/nonexistent"):
            response = client.request(method, path)
            assert response.status_code == 405, (method, path)
    assert state["changed"] is False


def test_readonly_strips_non_get_routes():
    app, _ = make_app(readonly=True)
    for route in app.router.routes:
        if isinstance(route, APIRoute):
            assert set(route.methods) <= {"GET", "HEAD"}
    assert "/write" not in [r.path for r in app.router.routes]


def test_full_mode_allows_writes():
    app, state = make_app(readonly=False)
    assert TestClient(app).post("/write").status_code == 200
    assert state["changed"] is True


def test_rate_limit_per_ip():
    app, _ = make_app(readonly=True, rpm=3)
    client = TestClient(app)
    assert [client.get("/read").status_code for _ in range(4)] == [200, 200, 200, 429]
    assert int(client.get("/read").headers["retry-after"]) >= 1


def test_rate_limiter_window_slides():
    limiter = RateLimiter(2)
    assert limiter.allow("a", 0)[0] and limiter.allow("a", 1)[0]
    assert not limiter.allow("a", 2)[0]
    assert limiter.allow("b", 2)[0]
    assert limiter.allow("a", 60.5)[0]


def test_rate_limiter_bounded_clients():
    limiter = RateLimiter(5, max_clients=3)
    for index in range(10):
        limiter.allow(f"ip{index}", 0)
    assert len(limiter._hits) <= 3


def test_access_log_records_ip_and_denials(tmp_path):
    log = tmp_path / "access.log"
    app, _ = make_app(readonly=True, log_path=str(log))
    client = TestClient(app)
    client.get("/read?x=1")
    client.post("/write")
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json() == {"hello": 1}
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    assert rows[0]["ip"] == "testclient" and rows[0]["status"] == 200 and rows[0]["query"] == "x=1"
    assert rows[1]["status"] == 405 and rows[1]["note"] == "readonly"
    assert {row.get("note") for row in rows if row["method"] == "WS"} == {"open", "closed"}


def test_forwarded_for_is_ignored(tmp_path):
    log = tmp_path / "access.log"
    app, _ = make_app(readonly=True, rpm=1, log_path=str(log))
    client = TestClient(app)
    assert client.get("/read", headers={"x-forwarded-for": "1.2.3.4"}).status_code == 200
    assert client.get("/read", headers={"x-forwarded-for": "5.6.7.8"}).status_code == 429
    assert all(json.loads(line)["ip"] == "testclient" for line in log.read_text().splitlines())


def test_install_uses_build_profile(monkeypatch):
    monkeypatch.setattr(access_guard.build_profile, "READONLY", True)
    app, state = make_app(readonly=False)
    access_guard.install(app)
    assert TestClient(app).post("/write").status_code == 405
    assert state["changed"] is False
