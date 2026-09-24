from __future__ import annotations

from dataclasses import replace
import time

from fastapi.testclient import TestClient

from backend.app import main as main_module
from backend.app import proxy as proxy_module
from backend.app.security import sign_payload, verify_payload
from backend.app.store import store


TEST_ORIGIN = "http://127.0.0.1:9001"


def _proxy_url(session: dict, target: str) -> str:
    return f"/api/v1/proxy/{session['session_id']}"


def _proxy_request(client: TestClient, session: dict, target: str, **kwargs):
    kwargs.setdefault("follow_redirects", False)
    return client.get(
        _proxy_url(session, target),
        params={"url": target, "token": session["session_token"]},
        **kwargs,
    )


def _login(client: TestClient) -> None:
    response = client.post(
        "/api/v1/admin/auth/login",
        json={"email": "owner@relaynorth.local", "password": "change-me-now"},
    )
    assert response.status_code == 200, response.text


def test_session_token_is_signed_and_required_for_proxy(client: TestClient, session_payload: dict) -> None:
    claims = verify_payload(session_payload["session_token"], main_module.settings.secret_key)
    assert claims == {
        "cell": "eu",
        "exp": session_payload["expires_at"],
        "sid": session_payload["session_id"],
    }

    missing_token = client.get(
        _proxy_url(session_payload, f"{TEST_ORIGIN}/"), params={"url": f"{TEST_ORIGIN}/"}
    )
    tampered_token = session_payload["session_token"] + "tampered"
    tampered = client.get(
        _proxy_url(session_payload, f"{TEST_ORIGIN}/"),
        params={"url": f"{TEST_ORIGIN}/", "token": tampered_token},
    )

    assert missing_token.status_code == 401
    assert tampered.status_code == 401


def test_allowlisted_get_rewrites_links_and_counts_usage(client: TestClient, session_payload: dict) -> None:
    before = client.get(f"/api/v1/sessions/{session_payload['session_id']}/status").json()
    response = _proxy_request(client, session_payload, f"{TEST_ORIGIN}/")

    assert response.status_code == 200
    assert "Owned proxy test origin" in response.text
    assert f"/api/v1/proxy/{session_payload['session_id']}?url=" in response.text
    after = client.get(f"/api/v1/sessions/{session_payload['session_id']}/status").json()
    assert after["request_count"] == before["request_count"] + 1
    assert after["bytes_out"] > 0


def test_form_urlencoded_post_is_forwarded(client: TestClient, session_payload: dict) -> None:
    target = f"{TEST_ORIGIN}/echo"
    response = client.post(
        _proxy_url(session_payload, target),
        params={"url": target, "token": session_payload["session_token"]},
        data={"message": "form relay works"},
    )

    assert response.status_code == 200
    assert "POST received" in response.text
    assert "form relay works" in response.text


def test_proxy_rejects_non_form_post_content_type(client: TestClient, session_payload: dict) -> None:
    target = f"{TEST_ORIGIN}/echo"
    response = client.post(
        _proxy_url(session_payload, target),
        params={"url": target, "token": session_payload["session_token"]},
        content='{"message":"not form encoded"}',
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 415
    assert response.json()["detail"] == "Only form-urlencoded POST requests are supported"


def test_redirect_to_private_or_unapproved_origin_is_revalidated(client: TestClient, session_payload: dict) -> None:
    private_redirect = _proxy_request(client, session_payload, f"{TEST_ORIGIN}/redirect-private")
    external_redirect = _proxy_request(client, session_payload, f"{TEST_ORIGIN}/redirect-external")

    assert private_redirect.status_code == 403
    assert external_redirect.status_code == 403


def test_cookie_jar_isolated_per_session(client: TestClient, session_payload: dict) -> None:
    second = client.post("/api/v1/sessions", json={"url": f"{TEST_ORIGIN}/", "region": "eu"}).json()
    cookie_target = f"{TEST_ORIGIN}/cookie"

    first_set = _proxy_request(client, session_payload, cookie_target)
    first_home = _proxy_request(client, session_payload, f"{TEST_ORIGIN}/")
    second_home = _proxy_request(client, second, f"{TEST_ORIGIN}/")

    assert first_set.status_code == 303
    assert first_home.status_code == 200
    assert "Cookie value: <strong>isolated</strong>" in first_home.text
    assert "Cookie value: <strong>not set</strong>" in second_home.text


def test_region_selection_and_unavailable_pinned_session(client: TestClient, session_payload: dict) -> None:
    assert session_payload["cell_id"] == "eu"
    assert session_payload["region"] == "Europe"

    _login(client)
    unavailable = client.patch("/api/v1/admin/regions/eu/state", params={"state": "unavailable"})
    assert unavailable.status_code == 200

    new_session = client.post("/api/v1/sessions", json={"url": f"{TEST_ORIGIN}/", "region": "eu"})
    existing_session = _proxy_request(client, session_payload, f"{TEST_ORIGIN}/")

    assert new_session.status_code == 503
    assert existing_session.status_code == 503


def test_draining_region_rejects_new_sessions(client: TestClient, session_payload: dict) -> None:
    _login(client)
    draining = client.patch("/api/v1/admin/regions/eu/state", params={"state": "draining"})

    assert draining.status_code == 200
    assert draining.json()["health_state"] == "draining"
    new_session = client.post("/api/v1/sessions", json={"url": f"{TEST_ORIGIN}/", "region": "eu"})
    assert new_session.status_code == 503


def test_expired_draining_region_rejects_pinned_session(client: TestClient, session_payload: dict) -> None:
    store.regions["eu"].health_state = "draining"
    store.regions["eu"].drain_deadline = int(time.time()) - 1

    response = _proxy_request(client, session_payload, f"{TEST_ORIGIN}/")

    assert response.status_code == 503


def test_session_creation_rate_limit_is_enforced(client: TestClient, monkeypatch) -> None:
    limited_settings = replace(main_module.settings, session_create_rate_limit=1)
    monkeypatch.setattr(main_module, "settings", limited_settings)
    monkeypatch.setattr(proxy_module, "settings", limited_settings)
    payload = {"url": f"{TEST_ORIGIN}/", "region": "eu"}

    first = client.post("/api/v1/sessions", json=payload)
    second = client.post("/api/v1/sessions", json=payload)

    assert first.status_code == 200
    assert second.status_code == 429


def test_per_session_request_limit_is_enforced(client: TestClient, session_payload: dict, monkeypatch) -> None:
    limited_settings = replace(main_module.settings, max_session_requests=1, max_concurrent_requests=1)
    monkeypatch.setattr(main_module, "settings", limited_settings)
    monkeypatch.setattr(proxy_module, "settings", limited_settings)

    first = _proxy_request(client, session_payload, f"{TEST_ORIGIN}/")
    second = _proxy_request(client, session_payload, f"{TEST_ORIGIN}/")

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["detail"] == "Session request limit reached"


def test_blog_draft_publish_archive_and_revision_workflow(client: TestClient) -> None:
    _login(client)
    slug = "integration-publishing-workflow"
    payload = {
        "title": "Integration publishing workflow",
        "slug": slug,
        "excerpt": "A test article for the publishing lifecycle.",
        "body_markdown": "# Draft heading\n\nA safe **published** note.",
        "category": "Guides",
        "tags": ["tests"],
        "status": "draft",
    }

    created = client.post("/api/v1/admin/posts", json=payload)
    assert created.status_code == 200, created.text
    assert client.get(f"/api/v1/blog/posts/{slug}").status_code == 404

    revisions = client.get(f"/api/v1/admin/posts/{slug}/revisions")
    assert revisions.status_code == 200
    assert len(revisions.json()) == 1

    published = client.post(f"/api/v1/admin/posts/{slug}/publish")
    assert published.status_code == 200
    assert published.json()["status"] == "published"
    # This service is API-only in the split deployment; the rendered blog page is
    # served by the public site. Assert the API the frontend consumes.
    public_post = client.get(f"/api/v1/blog/posts/{slug}")
    assert public_post.status_code == 200
    assert "<strong>published</strong>" in public_post.json()["body_html"]
    assert "Integration publishing workflow" in public_post.json()["title"]

    archived = client.post(f"/api/v1/admin/posts/{slug}/archive")
    assert archived.status_code == 200
    assert client.get(f"/api/v1/blog/posts/{slug}").status_code == 404


def test_blog_publish_requires_admin_authentication(client: TestClient) -> None:
    response = client.post("/api/v1/admin/posts/missing/publish")

    assert response.status_code == 401


def test_blog_editor_role_can_publish_but_support_role_cannot(client: TestClient) -> None:
    slug = "role-publishing-workflow"
    post = {
        "title": "Role publishing workflow",
        "slug": slug,
        "excerpt": "Role test.",
        "body_markdown": "Role test body",
        "category": "Security",
        "tags": [],
        "status": "draft",
    }
    editor_token = sign_payload(
        {"sub": "editor@relaynorth.local", "role": "editor", "exp": int(time.time()) + 600},
        main_module.settings.secret_key,
    )
    support_token = sign_payload(
        {"sub": "support@relaynorth.local", "role": "support", "exp": int(time.time()) + 600},
        main_module.settings.secret_key,
    )

    client.cookies.set("relay_admin", editor_token)
    created = client.post("/api/v1/admin/posts", json=post)
    assert created.status_code == 200, created.text
    client.cookies.set("relay_admin", support_token)
    forbidden = client.post(f"/api/v1/admin/posts/{slug}/publish")

    assert forbidden.status_code == 403
