from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import quote, urlsplit

from fastapi import Cookie, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import settings
from .markdown import render_markdown
from .proxy import fetch_for_session, validate_url
from .security import sign_payload, verify_payload
from .store import BlogPost, store


ROOT = Path(__file__).resolve().parents[2]
PUBLIC = ROOT / "public"
ADMIN = ROOT / "admin"
ADMIN_DIST = ADMIN / "dist"

@asynccontextmanager
async def lifespan(_: FastAPI):
    store.initialize()
    try:
        yield
    finally:
        store.close()


app = FastAPI(title="RelayNorth API", version="0.1.0", lifespan=lifespan)

# Optional CORS for split deployments: set ALLOWED_APP_ORIGINS to the public/admin
# origins (comma-separated) when the frontends are hosted as separate services.
_app_origins = [o.strip() for o in os.getenv("ALLOWED_APP_ORIGINS", "").split(",") if o.strip()]
if _app_origins:
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_app_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# The bundled public site and built admin console are optional. When this service
# runs API-only (a separate Railway service), those directories are absent, so the
# static mounts are skipped instead of raising at startup.
if PUBLIC.exists():
    app.mount("/static", StaticFiles(directory=PUBLIC), name="static")
_admin_assets = ADMIN_DIST if ADMIN_DIST.exists() else (ADMIN if ADMIN.exists() else None)
if _admin_assets is not None:
    app.mount("/admin/assets", StaticFiles(directory=_admin_assets), name="admin-assets")


class SessionCreate(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    region: Literal["eu", "us", "apac"] = "eu"


class LoginInput(BaseModel):
    email: str
    password: str


class PostInput(BaseModel):
    title: str = Field(min_length=3, max_length=140)
    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    excerpt: str = Field(max_length=280)
    body_markdown: str = Field(min_length=1, max_length=50_000)
    category: str = "Guides"
    tags: list[str] = Field(default_factory=list)
    seo_title: str = ""
    seo_description: str = ""
    status: Literal["draft", "scheduled", "published", "archived"] = "draft"


class AbuseInput(BaseModel):
    category: str = Field(min_length=2, max_length=80)
    description: str = Field(min_length=4, max_length=2_000)


def public_page(filename: str) -> FileResponse:
    # In the split deployment the public site is its own service; if its files are
    # absent this API-only instance answers 404 instead of raising.
    path = PUBLIC / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="This page is served by the public site service")
    return FileResponse(path)


def admin_page() -> FileResponse:
    index = ADMIN_DIST / "index.html" if ADMIN_DIST.exists() else ADMIN / "index.html"
    if not index.exists():
        raise HTTPException(status_code=404, detail="The admin console is served by its own service")
    return FileResponse(index)


def admin_claim(admin_session: str | None = Cookie(default=None, alias="relay_admin")) -> dict | None:
    if not admin_session:
        return None
    return verify_payload(admin_session, settings.secret_key)


def require_admin(claim: dict | None = Depends(admin_claim)) -> dict:
    if not claim:
        raise HTTPException(status_code=401, detail="Admin authentication required")
    return claim


def require_role(*roles: str):
    def dependency(claim: dict = Depends(require_admin)) -> dict:
        if claim.get("role") not in roles:
            raise HTTPException(status_code=403, detail="Insufficient role for this action")
        return claim

    return dependency


@app.get("/", response_class=HTMLResponse)
def home() -> FileResponse:
    return public_page("index.html")


@app.get("/browse", response_class=HTMLResponse)
def browse() -> FileResponse:
    return public_page("browse.html")


@app.get("/faq", response_class=HTMLResponse)
def faq() -> FileResponse:
    return public_page("faq.html")


@app.get("/privacy", response_class=HTMLResponse)
def privacy() -> FileResponse:
    return public_page("privacy.html")


@app.get("/terms", response_class=HTMLResponse)
def terms() -> FileResponse:
    return public_page("terms.html")


@app.get("/acceptable-use", response_class=HTMLResponse)
def acceptable_use() -> FileResponse:
    return public_page("acceptable-use.html")


@app.get("/copyright", response_class=HTMLResponse)
def copyright_page() -> FileResponse:
    return public_page("copyright.html")


@app.get("/status", response_class=HTMLResponse)
def status_page() -> FileResponse:
    return public_page("status.html")


@app.get("/blog", response_class=HTMLResponse)
def blog_index() -> HTMLResponse:
    shell = PUBLIC / "blog-shell.html"
    if not shell.exists():
        raise HTTPException(status_code=404, detail="The blog is served by the public site service")
    posts = [post for post in store.posts.values() if post.status == "published"]
    cards = "".join(
        f'<a class="article-card" href="/blog/{post.slug}"><span class="eyebrow">{post.category} · 6 min read</span><h2>{post.title}</h2><p>{post.excerpt}</p><span class="card-arrow">Read the field note ↗</span></a>'
        for post in posts
    )
    return HTMLResponse(shell.read_text().replace("{{BLOG_CARDS}}", cards))


@app.get("/blog/{slug}", response_class=HTMLResponse)
def blog_article(slug: str) -> HTMLResponse:
    post = store.posts.get(slug)
    if not post or post.status != "published":
        raise HTTPException(status_code=404, detail="Article not found")
    shell = PUBLIC / "article-shell.html"
    if not shell.exists():
        raise HTTPException(status_code=404, detail="The blog is served by the public site service")
    page = shell.read_text()
    page = page.replace("{{TITLE}}", post.title).replace("{{EXCERPT}}", post.excerpt).replace("{{CATEGORY}}", post.category)
    page = page.replace("{{BODY}}", render_markdown(post.body_markdown)).replace("{{DATE}}", time.strftime("%B %d, %Y", time.localtime(post.published_at or post.updated_at)))
    return HTMLResponse(page)


@app.get("/admin", response_class=HTMLResponse)
@app.get("/admin/", response_class=HTMLResponse)
def admin_root() -> FileResponse:
    return admin_page()


@app.get("/admin/{path:path}", response_class=HTMLResponse, include_in_schema=False)
def admin_deep_link(path: str) -> FileResponse:
    """Serve the React shell for client-side admin routes."""
    return admin_page()


@app.get("/healthz")
def healthz() -> JSONResponse:
    dependencies = store.dependency_status()
    # The control plane keeps serving on the in-memory fallback even when the
    # optional Postgres/Redis dependencies are unreachable, so report healthy
    # (200) whenever it can serve requests and surface a `degraded` flag for
    # observability instead of failing load-balancer probes.
    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "degraded": not dependencies["ok"],
            "service": "relaynorth-control-plane",
            "dependencies": dependencies,
            "timestamp": int(time.time()),
        },
    )


@app.get("/api/v1/regions")
def regions() -> list[dict]:
    return [region.__dict__ for region in store.regions.values()]


@app.post("/api/v1/sessions")
def create_session(payload: SessionCreate, request: Request) -> dict:
    client_ip = request.client.host if request.client else "unknown"
    if not store.allow_rate(f"session-create:ip:{client_ip}", settings.session_create_rate_limit):
        raise HTTPException(status_code=429, detail="Session creation rate limit reached")
    region = store.regions[payload.region]
    if region.health_state != "ready":
        raise HTTPException(status_code=503, detail="Selected relay region is not accepting new sessions")
    validated = validate_url(payload.url)
    origin = f"{urlsplit(validated).scheme}://{urlsplit(validated).netloc}".rstrip("/")
    worker = settings.worker_for_region(region.cell_id)
    if settings.require_regional_workers and worker is None:
        raise HTTPException(status_code=503, detail="Selected relay region has no healthy worker")
    worker_id, worker_url, configured_egress_ip = worker or (None, None, region.egress_ip_pool[0] if region.egress_ip_pool else None)
    session = store.create_session(
        region.cell_id,
        origin,
        settings.max_session_seconds,
        client_ip,
        worker_id=worker_id,
        worker_url=worker_url,
        egress_ip=configured_egress_ip,
    )
    session_token = sign_payload(
        {"sid": session.session_id, "cell": session.cell_id, "exp": session.expires_at}, settings.secret_key
    )
    egress_ip = session.egress_ip or (region.egress_ip_pool[0] if region.egress_ip_pool else None)
    return {
        "session_id": session.session_id,
        "cell_id": session.cell_id,
        "region": region.display_name,
        "egress_ip": egress_ip,
        "expires_at": session.expires_at,
        "session_token": session_token,
        "browse_url": f"/browse?session={session.session_id}&token={quote(session_token, safe='')}&url={quote(validated, safe='')}",
    }


@app.get("/api/v1/sessions/{session_id}/status")
def session_status(session_id: str) -> dict:
    session = store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    region = store.regions.get(session.cell_id)
    egress_ip = session.egress_ip or (region.egress_ip_pool[0] if region and region.egress_ip_pool else None)
    return {
        "session_id": session.session_id,
        "cell_id": session.cell_id,
        "region": region.display_name if region else session.cell_id,
        "egress_ip": egress_ip,
        "origin": session.origin,
        "worker_id": session.worker_id,
        "expires_at": session.expires_at,
        "request_count": session.request_count,
        "bytes_out": session.bytes_out,
    }


@app.api_route("/api/v1/proxy/{session_id}", methods=["GET", "POST"])
async def proxy(session_id: str, url: str, request: Request, token: str | None = None) -> Response:
    session = store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    claim = verify_payload(token or "", settings.secret_key)
    if not claim or claim.get("sid") != session_id or claim.get("cell") != session.cell_id:
        raise HTTPException(status_code=401, detail="A valid session token is required")
    region = store.regions.get(session.cell_id)
    if not region or region.health_state == "unavailable" or (
        region.health_state == "draining" and region.drain_deadline and region.drain_deadline <= int(time.time())
    ):
        raise HTTPException(status_code=503, detail="The pinned relay cell is unavailable")
    client_ip = request.client.host if request.client else "unknown"
    rejection = store.begin_request(session, client_ip, settings.max_session_requests, settings.max_concurrent_requests)
    if rejection:
        store.increment_blocked()
        raise HTTPException(status_code=429, detail=rejection)
    body = None
    content_type = None
    if request.method == "POST":
        content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
        if content_type != "application/x-www-form-urlencoded":
            store.end_request(session.session_id)
            raise HTTPException(status_code=415, detail="Only form-urlencoded POST requests are supported")
        body = await request.body()
        if len(body) > 32_000:
            store.end_request(session.session_id)
            raise HTTPException(status_code=413, detail="Form body exceeds the configured limit")
    try:
        status_code, headers, content = await fetch_for_session(session, url, request.method, body, content_type)
    finally:
        store.end_request(session.session_id)
    return Response(content=content, status_code=status_code, headers=headers)


@app.post("/api/v1/abuse-reports")
def create_abuse_report(payload: AbuseInput) -> dict:
    report_id = f"abuse_{len(store.abuse_reports) + 1:04d}"
    from .store import AbuseReport

    store.add_abuse_report(AbuseReport(report_id, payload.category, payload.description))
    return {"report_id": report_id, "status": "received"}


@app.get("/api/v1/blog/posts")
def blog_posts() -> list[dict]:
    return [post.__dict__ for post in store.posts.values() if post.status == "published"]


@app.get("/api/v1/blog/posts/{slug}")
def blog_post(slug: str) -> dict:
    post = store.posts.get(slug)
    if not post or post.status != "published":
        raise HTTPException(status_code=404, detail="Article not found")
    return {**post.__dict__, "body_html": render_markdown(post.body_markdown)}


@app.post("/api/v1/admin/auth/login")
def admin_login(payload: LoginInput, response: Response) -> dict:
    if payload.email.lower() != settings.admin_email.lower() or payload.password != settings.admin_password:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = sign_payload({"sub": payload.email, "role": "owner", "exp": int(time.time()) + 8 * 60 * 60}, settings.secret_key)
    response.set_cookie("relay_admin", token, httponly=True, secure=settings.app_env == "production", samesite=settings.admin_cookie_samesite, max_age=8 * 60 * 60)
    store.add_audit(payload.email, "admin.login", "admin")
    return {"email": payload.email, "role": "owner"}


@app.post("/api/v1/admin/auth/logout")
def admin_logout(response: Response) -> dict:
    response.delete_cookie("relay_admin")
    return {"ok": True}


@app.get("/api/v1/admin/me")
def admin_me(claim: dict = Depends(require_admin)) -> dict:
    return {"email": claim.get("sub"), "role": claim.get("role")}


def serialize_post(post: BlogPost) -> dict:
    return {key: value for key, value in post.__dict__.items() if key != "revisions"} | {"revision_count": len(post.revisions)}


@app.get("/api/v1/admin/posts")
def admin_posts(_: dict = Depends(require_role("owner", "editor"))) -> list[dict]:
    return [serialize_post(post) for post in store.posts.values()]


@app.post("/api/v1/admin/posts")
def admin_create_post(payload: PostInput, claim: dict = Depends(require_role("owner", "editor"))) -> dict:
    if payload.slug in store.posts:
        raise HTTPException(status_code=409, detail="Slug already exists")
    now = int(time.time())
    post = BlogPost(f"post_{len(store.posts) + 1:03d}", payload.slug, payload.title, payload.excerpt, payload.body_markdown, payload.status, payload.category, payload.tags, payload.seo_title or payload.title, payload.seo_description or payload.excerpt, claim["sub"], now, now, now if payload.status == "published" else None)
    post.revisions.append({"revision_id": f"rev_{len(post.revisions) + 1}", "body_markdown": payload.body_markdown, "created_at": now, "author": claim["sub"]})
    store.save_post(post)
    store.add_audit(claim["sub"], "post.create", post.slug)
    return serialize_post(post)


@app.patch("/api/v1/admin/posts/{slug}")
def admin_update_post(slug: str, payload: PostInput, claim: dict = Depends(require_role("owner", "editor"))) -> dict:
    post = store.posts.get(slug)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    now = int(time.time())
    post.title, post.slug, post.excerpt, post.body_markdown = payload.title, payload.slug, payload.excerpt, payload.body_markdown
    post.category, post.tags, post.status = payload.category, payload.tags, payload.status
    post.seo_title, post.seo_description, post.updated_at = payload.seo_title or payload.title, payload.seo_description or payload.excerpt, now
    post.published_at = post.published_at or (now if payload.status == "published" else None)
    post.revisions.append({"revision_id": f"rev_{len(post.revisions) + 1}", "body_markdown": payload.body_markdown, "created_at": now, "author": claim["sub"]})
    store.save_post(post, previous_slug=slug)
    store.add_audit(claim["sub"], "post.update", post.slug)
    return serialize_post(post)


@app.post("/api/v1/admin/posts/{slug}/publish")
def admin_publish_post(slug: str, claim: dict = Depends(require_role("owner", "editor"))) -> dict:
    post = store.posts.get(slug)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    post.status, post.published_at, post.updated_at = "published", int(time.time()), int(time.time())
    store.save_post(post)
    store.add_audit(claim["sub"], "post.publish", slug)
    return serialize_post(post)


@app.post("/api/v1/admin/posts/{slug}/archive")
def admin_archive_post(slug: str, claim: dict = Depends(require_role("owner", "editor"))) -> dict:
    post = store.posts.get(slug)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    post.status, post.updated_at = "archived", int(time.time())
    store.save_post(post)
    store.add_audit(claim["sub"], "post.archive", slug)
    return serialize_post(post)


@app.get("/api/v1/admin/posts/{slug}/revisions")
def admin_revisions(slug: str, _: dict = Depends(require_role("owner", "editor"))) -> list[dict]:
    post = store.posts.get(slug)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    return post.revisions


@app.get("/api/v1/admin/regions")
def admin_regions(_: dict = Depends(require_role("owner", "security"))) -> list[dict]:
    return [region.__dict__ for region in store.regions.values()]


@app.patch("/api/v1/admin/regions/{cell_id}/state")
def admin_region_state(cell_id: str, state: Literal["ready", "draining", "unavailable"], claim: dict = Depends(require_role("owner", "security"))) -> dict:
    region = store.regions.get(cell_id)
    if not region:
        raise HTTPException(status_code=404, detail="Region not found")
    region = store.set_region_state(cell_id, state, int(time.time()) + 120 if state == "draining" else None)
    store.add_audit(claim["sub"], "region.state", f"{cell_id}:{state}")
    return region.__dict__


@app.get("/api/v1/admin/abuse-reports")
def admin_abuse(_: dict = Depends(require_role("owner", "support", "security"))) -> list[dict]:
    return [report.__dict__ for report in store.abuse_reports.values()]


@app.get("/api/v1/admin/usage")
def admin_usage(_: dict = Depends(require_role("owner", "support", "security"))) -> dict:
    return {**store.usage, "regions": [{"cell_id": region.cell_id, "state": region.health_state, "capacity": region.capacity} for region in store.regions.values()]}


@app.get("/api/v1/admin/audit-events")
def admin_audit(_: dict = Depends(require_role("owner", "security"))) -> list[dict]:
    return store.audit_events[:50]
