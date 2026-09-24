from __future__ import annotations

import copy

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app import main as main_module
from backend.app import proxy as proxy_module
from backend.app.config import Settings
from backend.app.store import store
from tests.owned_origin import app as owned_origin_app


TEST_ORIGIN = "http://127.0.0.1:9001"


class OwnedOriginClient(httpx.AsyncClient):
    """Keep proxy integration tests in-process when socket binding is unavailable."""

    def __init__(self, *args, **kwargs):
        kwargs["transport"] = httpx.ASGITransport(app=owned_origin_app)
        super().__init__(*args, **kwargs)


@pytest.fixture(autouse=True)
def isolated_memory_store(monkeypatch: pytest.MonkeyPatch):
    """Keep global development state from leaking between integration tests."""
    # Rate buckets and in-flight counters are transient process state. Reset them
    # before each test so one test's traffic cannot affect another test's limits.
    store.rate_windows.clear()
    store.active_requests.clear()
    if getattr(store, "redis", None) is not None:
        try:
            transient_keys = list(store.redis.scan_iter(match="relaynorth:rate:*")) + list(
                store.redis.scan_iter(match="relaynorth:active:*")
            )
            if transient_keys:
                store.redis.delete(*transient_keys)
        except Exception:
            pass
    snapshot = {
        "regions": copy.deepcopy(store.regions),
        "sessions": copy.deepcopy(store.sessions),
        "posts": copy.deepcopy(store.posts),
        "abuse_reports": copy.deepcopy(store.abuse_reports),
        "audit_events": copy.deepcopy(store.audit_events),
        "usage": copy.deepcopy(store.usage),
        "rate_windows": copy.deepcopy(store.rate_windows),
        "active_requests": copy.deepcopy(store.active_requests),
    }
    test_settings = Settings(
        secret_key="integration-test-secret",
        admin_email="owner@relaynorth.local",
        admin_password="change-me-now",
        allowed_proxy_origins=(TEST_ORIGIN,),
        allow_local_test_origin=True,
        max_session_seconds=900,
        session_create_rate_limit=10,
        proxy_request_rate_limit=60,
        max_session_requests=120,
        max_concurrent_requests=2,
    )
    monkeypatch.setattr(main_module, "settings", test_settings)
    monkeypatch.setattr(proxy_module, "settings", test_settings)
    yield
    store.regions.clear()
    store.regions.update(snapshot["regions"])
    store.sessions.clear()
    store.sessions.update(snapshot["sessions"])
    store.posts.clear()
    store.posts.update(snapshot["posts"])
    store.abuse_reports.clear()
    store.abuse_reports.update(snapshot["abuse_reports"])
    store.audit_events[:] = snapshot["audit_events"]
    store.usage.clear()
    store.usage.update(snapshot["usage"])
    store.rate_windows.clear()
    store.rate_windows.update(snapshot["rate_windows"])
    store.active_requests.clear()
    store.active_requests.update(snapshot["active_requests"])


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(proxy_module.httpx, "AsyncClient", OwnedOriginClient)
    with TestClient(main_module.app) as test_client:
        yield test_client


@pytest.fixture
def session_payload(client: TestClient) -> dict:
    response = client.post("/api/v1/sessions", json={"url": f"{TEST_ORIGIN}/", "region": "eu"})
    assert response.status_code == 200, response.text
    return response.json()
