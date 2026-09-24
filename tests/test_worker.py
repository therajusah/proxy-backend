from __future__ import annotations

import base64

import httpx
from fastapi.testclient import TestClient

from worker import main as worker_module


def test_worker_requires_internal_auth(monkeypatch):
    monkeypatch.setattr(worker_module, "WORKER_SHARED_SECRET", "test-worker-secret")
    client = TestClient(worker_module.app)
    response = client.post("/internal/v1/heartbeat")
    assert response.status_code == 401


def test_worker_fetch_returns_bounded_upstream_response(monkeypatch):
    monkeypatch.setattr(worker_module, "WORKER_SHARED_SECRET", "test-worker-secret")

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def request(self, *_args, **_kwargs):
            return httpx.Response(
                200,
                headers={"content-type": "text/html; charset=utf-8", "set-cookie": "relay=ok; Path=/"},
                content=b"<h1>worker response</h1>",
            )

    monkeypatch.setattr(worker_module.httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    client = TestClient(worker_module.app)
    response = client.post(
        "/internal/v1/fetch",
        headers={"Authorization": "Bearer test-worker-secret"},
        json={
            "session_id": "session-123456",
            "target_url": "http://127.0.0.1:9001/",
            "method": "GET",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status_code"] == 200
    assert base64.b64decode(body["body_b64"]) == b"<h1>worker response</h1>"
    assert body["set_cookies"] == ["relay=ok; Path=/"]
