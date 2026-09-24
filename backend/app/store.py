from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError

from .config import settings
from .db import Base, SessionLocal, engine
from .models import (
    AbuseReportModel,
    AuditEventModel,
    BlogCategoryModel,
    BlogPostModel,
    BlogRevisionModel,
    RegionModel,
    RelaySessionModel,
    RoleModel,
    UsageAggregateModel,
    UserModel,
)

logger = logging.getLogger(__name__)


@dataclass
class Region:
    cell_id: str
    display_name: str
    country: str
    health_state: str = "ready"
    capacity: int = 72
    egress_ip_pool: list[str] = field(default_factory=list)
    drain_deadline: int | None = None
    policy_version: int = 1


@dataclass
class Session:
    session_id: str
    cell_id: str
    origin: str
    created_at: int
    expires_at: int
    client_ip: str
    cookies: dict[str, str] = field(default_factory=dict)
    request_count: int = 0
    bytes_out: int = 0
    client_ip_hash: str | None = None
    worker_id: str | None = None
    worker_url: str | None = None
    egress_ip: str | None = None


@dataclass
class BlogPost:
    post_id: str
    slug: str
    title: str
    excerpt: str
    body_markdown: str
    status: str
    category: str
    tags: list[str]
    seo_title: str
    seo_description: str
    author: str
    created_at: int
    updated_at: int
    published_at: int | None = None
    revisions: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class AbuseReport:
    report_id: str
    category: str
    description: str
    status: str = "open"
    created_at: int = field(default_factory=lambda: int(time.time()))


def _utc_datetime(epoch: int | None = None) -> datetime:
    return datetime.fromtimestamp(epoch or int(time.time()), tz=timezone.utc).replace(tzinfo=None)


def _epoch(value: datetime | None) -> int | None:
    return int(value.replace(tzinfo=timezone.utc).timestamp()) if value else None


def _ip_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class MemoryStore:
    """In-memory state used when persistence services are not configured or reachable."""

    def __init__(self) -> None:
        now = int(time.time())
        self.regions: dict[str, Region] = {
            "eu": Region("eu", "Europe", "Europe", egress_ip_pool=[settings.worker_egress_ips["eu"]]),
            "us": Region("us", "United States", "United States", egress_ip_pool=[settings.worker_egress_ips["us"]]),
            "apac": Region("apac", "Asia", "Asia", egress_ip_pool=[settings.worker_egress_ips["apac"]]),
        }
        self.sessions: dict[str, Session] = {}
        self.posts: dict[str, BlogPost] = {}
        self.abuse_reports: dict[str, AbuseReport] = {}
        self.audit_events: list[dict[str, Any]] = []
        self.usage: dict[str, int] = {"active_sessions": 0, "requests": 0, "bytes_out": 0, "blocked": 0}
        self.rate_windows: dict[str, tuple[int, int]] = {}
        self.active_requests: dict[str, int] = {}
        self.ip_bytes: dict[str, int] = {}
        self._seed_posts(now)

    def _seed_posts(self, now: int) -> None:
        self.posts["best-free-web-proxies-2026"] = BlogPost(
            post_id="post_001",
            slug="best-free-web-proxies-2026",
            title="A calmer way to think about web proxies",
            excerpt="What a browser relay can protect, what it cannot, and how to test one without trusting marketing claims.",
            body_markdown=(
                "# A calmer way to think about web proxies\n\n"
                "A web proxy covers a browser session, not your whole device. It can keep a destination from seeing your direct IP address, but it cannot make a logged-in account anonymous.\n\n"
                "## Start with the boundary\n\n"
                "The safest relay is explicit about its limits, uses short-lived sessions, and keeps the browsing path separate from its control plane."
            ),
            status="published",
            category="Guides",
            tags=["privacy", "proxy basics"],
            seo_title="A calmer way to think about web proxies | RelayNorth",
            seo_description="A plain-language guide to web proxy privacy, boundaries, and safe testing.",
            author="RelayNorth Editorial",
            created_at=now,
            updated_at=now,
            published_at=now,
        )
        self.posts["proxysite-com-alternative"] = BlogPost(
            post_id="post_002",
            slug="proxysite-com-alternative",
            title="What to compare in a web-proxy alternative",
            excerpt="Page fidelity, cookie boundaries, region choice, and honest limits matter more than a long list of buttons.",
            body_markdown=(
                "# What to compare in a web-proxy alternative\n\n"
                "A useful comparison starts with page fidelity and privacy boundaries, not with promises that every site will load perfectly.\n\n"
                "## A short checklist\n\n"
                "- Does the service explain its session boundary?\n"
                "- Are destinations and ports controlled?\n"
                "- Can the operator respond to abuse quickly?"
            ),
            status="published",
            category="Comparisons",
            tags=["comparisons", "security"],
            seo_title="What to compare in a web-proxy alternative | RelayNorth",
            seo_description="A practical checklist for comparing browser proxy services.",
            author="RelayNorth Editorial",
            created_at=now,
            updated_at=now,
            published_at=now,
        )

    def add_audit(self, actor: str, action: str, target: str) -> None:
        self.audit_events.insert(0, {"id": str(uuid.uuid4()), "actor": actor, "action": action, "target": target, "at": int(time.time())})

    def allow_rate(self, key: str, limit: int, window_seconds: int = 60) -> bool:
        now = int(time.time())
        window_start, count = self.rate_windows.get(key, (now, 0))
        if now - window_start >= window_seconds:
            window_start, count = now, 0
        if count >= limit:
            self.rate_windows[key] = (window_start, count)
            return False
        self.rate_windows[key] = (window_start, count + 1)
        return True

    def create_session(
        self,
        cell_id: str,
        origin: str,
        ttl: int,
        client_ip: str,
        worker_id: str | None = None,
        worker_url: str | None = None,
        egress_ip: str | None = None,
    ) -> Session:
        now = int(time.time())
        session = Session(
            uuid.uuid4().hex,
            cell_id,
            origin,
            now,
            now + ttl,
            client_ip,
            client_ip_hash=_ip_hash(client_ip),
            worker_id=worker_id,
            worker_url=worker_url,
            egress_ip=egress_ip,
        )
        self.sessions[session.session_id] = session
        self.usage["active_sessions"] += 1
        return session

    def begin_request(self, session: Session, client_ip: str, request_limit: int, concurrency_limit: int) -> str | None:
        if session.request_count >= request_limit:
            return "Session request limit reached"
        if (session.client_ip_hash or _ip_hash(session.client_ip)) != _ip_hash(client_ip):
            return "Session client binding mismatch"
        if self.bandwidth_used(client_ip) >= settings.max_bytes_per_ip:
            return "Bandwidth limit reached for this IP"
        if not self.allow_rate(f"proxy:ip:{client_ip}", request_limit):
            return "Request rate limit reached"
        if not self.allow_rate(f"proxy:session:{session.session_id}", request_limit):
            return "Session rate limit reached"
        active = self.active_requests.get(session.session_id, 0)
        if active >= concurrency_limit:
            return "Too many concurrent requests"
        self.active_requests[session.session_id] = active + 1
        return None

    def end_request(self, session_id: str) -> None:
        active = self.active_requests.get(session_id, 0)
        if active <= 1:
            self.active_requests.pop(session_id, None)
        else:
            self.active_requests[session_id] = active - 1

    def get_session(self, session_id: str) -> Session | None:
        session = self.sessions.get(session_id)
        if not session:
            return None
        if session.expires_at < int(time.time()):
            self.sessions.pop(session_id, None)
            self.usage["active_sessions"] = max(0, self.usage["active_sessions"] - 1)
            return None
        return session

    def bandwidth_used(self, client_ip: str) -> int:
        """Cumulative egress bytes already served to this client IP."""
        return self.ip_bytes.get(_ip_hash(client_ip), 0)

    def _add_ip_bytes(self, ip_hash: str, response_bytes: int) -> int:
        total = self.ip_bytes.get(ip_hash, 0) + response_bytes
        self.ip_bytes[ip_hash] = total
        return total

    def record_usage(self, session: Session, response_bytes: int) -> None:
        session.request_count += 1
        session.bytes_out += response_bytes
        self.usage["requests"] += 1
        self.usage["bytes_out"] += response_bytes
        self._add_ip_bytes(session.client_ip_hash or _ip_hash(session.client_ip), response_bytes)


class PersistentStore(MemoryStore):
    """Memory-compatible cache backed by PostgreSQL and coordinated by Redis."""

    def __init__(self) -> None:
        super().__init__()
        self.db_configured = engine is not None
        self.redis_configured = bool(settings.redis_url)
        self.db_ready = False
        self.redis_ready = False
        self.db_error: str | None = None
        self.redis_error: str | None = None
        self._initialized = False
        self.redis: Any | None = None
        if settings.redis_url:
            try:
                from redis import Redis

                self.redis = Redis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=1, socket_timeout=1)
            except (ImportError, ValueError) as exc:
                self.redis_error = type(exc).__name__

    def initialize(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        if self.db_configured:
            try:
                assert engine is not None
                Base.metadata.create_all(engine)
                self._bootstrap_database()
                self._load_database_state()
                self.db_ready = True
                self.db_error = None
            except (SQLAlchemyError, OSError, AssertionError) as exc:
                self.db_error = type(exc).__name__
                logger.warning("PostgreSQL unavailable; using in-memory fallback: %s", self.db_error)
        if self.redis is not None:
            self._probe_redis()

    def _probe_database(self) -> bool:
        if not self.db_configured or engine is None:
            return False
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            self.db_ready, self.db_error = True, None
            return True
        except (SQLAlchemyError, OSError) as exc:
            self.db_ready, self.db_error = False, type(exc).__name__
            return False

    def _probe_redis(self) -> bool:
        if self.redis is None:
            return False
        try:
            self.redis.ping()
            self.redis_ready, self.redis_error = True, None
            return True
        except Exception as exc:
            self.redis_ready, self.redis_error = False, type(exc).__name__
            return False

    def dependency_status(self) -> dict[str, Any]:
        if self.db_configured:
            self._probe_database()
        if self.redis_configured:
            self._probe_redis()
        database = {
            "configured": self.db_configured,
            "ok": self.db_ready if self.db_configured else True,
            "backend": "postgresql" if self.db_configured else "memory-fallback",
        }
        redis = {
            "configured": self.redis_configured,
            "ok": self.redis_ready if self.redis_configured else True,
            "backend": "redis" if self.redis_configured else "memory-fallback",
        }
        ready = bool(database["ok"] and redis["ok"])
        if ready and database["configured"] and redis["configured"]:
            mode = "postgresql+redis"
        elif database["ok"] and database["configured"]:
            mode = "postgresql+memory-rate-state"
        else:
            mode = "memory-fallback"
        return {
            "ok": ready,
            "mode": mode,
            "database": database | ({"error": self.db_error} if self.db_error else {}),
            "redis": redis | ({"error": self.redis_error} if self.redis_error else {}),
        }

    def close(self) -> None:
        if self.redis is not None:
            try:
                self.redis.close()
            except Exception:
                pass
        if engine is not None:
            engine.dispose()

    def _bootstrap_database(self) -> None:
        if SessionLocal is None:
            return
        with SessionLocal() as database:
            roles = {}
            for name in ("owner", "editor", "support", "security"):
                role = database.scalar(select(RoleModel).where(RoleModel.name == name))
                if role is None:
                    role = RoleModel(id=f"role_{name}", name=name)
                    database.add(role)
                roles[name] = role
            database.flush()
            admin = database.scalar(select(UserModel).where(UserModel.email == settings.admin_email.lower()))
            if admin is None:
                from .security import password_digest

                admin = UserModel(id="user_admin", email=settings.admin_email.lower(), password_hash=password_digest(settings.admin_password), role_id=roles["owner"].id)
                database.add(admin)
            else:
                admin.role_id = roles["owner"].id
            for region in self.regions.values():
                if database.get(RegionModel, region.cell_id) is None:
                    database.add(RegionModel(**self._region_values(region)))
            database.flush()
            for name in {post.category for post in self.posts.values()}:
                if database.scalar(select(BlogCategoryModel).where(BlogCategoryModel.name == name)) is None:
                    database.add(BlogCategoryModel(id=f"category_{uuid.uuid4().hex[:12]}", name=name))
            database.flush()
            if database.scalar(select(BlogPostModel).limit(1)) is None:
                for post in self.posts.values():
                    category = database.scalar(select(BlogCategoryModel).where(BlogCategoryModel.name == post.category))
                    database.add(self._post_model(post, category.id, admin.id))
                    for revision in post.revisions:
                        database.add(self._revision_model(post, revision, admin.id))
            database.commit()

    @staticmethod
    def _region_values(region: Region) -> dict[str, Any]:
        return {
            "cell_id": region.cell_id,
            "display_name": region.display_name,
            "country": region.country,
            "health_state": region.health_state,
            "capacity": region.capacity,
            "egress_ip_pool": region.egress_ip_pool,
            "drain_deadline": _utc_datetime(region.drain_deadline) if region.drain_deadline else None,
            "policy_version": region.policy_version,
        }

    @staticmethod
    def _post_model(post: BlogPost, category_id: str, author_id: str) -> BlogPostModel:
        return BlogPostModel(
            id=post.post_id,
            slug=post.slug,
            title=post.title,
            excerpt=post.excerpt,
            body_markdown=post.body_markdown,
            status=post.status,
            category_id=category_id,
            tags=post.tags,
            seo_title=post.seo_title,
            seo_description=post.seo_description,
            author_id=author_id,
            created_at=_utc_datetime(post.created_at),
            updated_at=_utc_datetime(post.updated_at),
            published_at=_utc_datetime(post.published_at) if post.published_at else None,
        )

    @staticmethod
    def _revision_model(post: BlogPost, revision: dict[str, Any], author_id: str) -> BlogRevisionModel:
        return BlogRevisionModel(id=revision["revision_id"], post_id=post.post_id, body_markdown=revision["body_markdown"], editor_id=author_id, created_at=_utc_datetime(revision.get("created_at")))

    def _load_database_state(self) -> None:
        if SessionLocal is None:
            return
        with SessionLocal() as database:
            for model in database.scalars(select(RegionModel)).all():
                self.regions[model.cell_id] = Region(model.cell_id, model.display_name, model.country, model.health_state, model.capacity, list(model.egress_ip_pool or []), _epoch(model.drain_deadline), model.policy_version)
            self.posts.clear()
            categories = {model.id: model.name for model in database.scalars(select(BlogCategoryModel)).all()}
            users = {model.id: model.email for model in database.scalars(select(UserModel)).all()}
            for model in database.scalars(select(BlogPostModel).order_by(BlogPostModel.created_at)).all():
                revisions = [
                    {"revision_id": revision.id, "body_markdown": revision.body_markdown, "created_at": _epoch(revision.created_at), "author": users.get(revision.editor_id, "unknown")}
                    for revision in database.scalars(select(BlogRevisionModel).where(BlogRevisionModel.post_id == model.id).order_by(BlogRevisionModel.created_at)).all()
                ]
                self.posts[model.slug] = BlogPost(model.id, model.slug, model.title, model.excerpt, model.body_markdown, model.status, categories.get(model.category_id, "Guides"), list(model.tags or []), model.seo_title, model.seo_description, users.get(model.author_id, "unknown"), _epoch(model.created_at) or int(time.time()), _epoch(model.updated_at) or int(time.time()), _epoch(model.published_at), revisions)
            self.abuse_reports = {model.id: AbuseReport(model.id, model.category, model.description, model.status, _epoch(model.created_at) or int(time.time())) for model in database.scalars(select(AbuseReportModel)).all()}
            totals = database.execute(select(func.coalesce(func.sum(UsageAggregateModel.requests), 0), func.coalesce(func.sum(UsageAggregateModel.bytes_out), 0), func.coalesce(func.sum(UsageAggregateModel.blocked), 0))).one()
            self.usage.update({"requests": int(totals[0]), "bytes_out": int(totals[1]), "blocked": int(totals[2])})
            self.usage["active_sessions"] = int(database.scalar(select(func.count(RelaySessionModel.id)).where(RelaySessionModel.expires_at > _utc_datetime())) or 0)
            self.audit_events = [{"id": model.id, "actor": model.actor_id, "action": model.action, "target": model.target, "at": _epoch(model.created_at)} for model in database.scalars(select(AuditEventModel).order_by(AuditEventModel.created_at.desc()).limit(100)).all()]
            for model in database.scalars(select(RelaySessionModel).where(RelaySessionModel.expires_at > _utc_datetime())).all():
                self.sessions[model.id] = Session(
                    model.id,
                    model.cell_id,
                    model.origin,
                    _epoch(model.created_at) or int(time.time()),
                    _epoch(model.expires_at) or int(time.time()),
                    "__persisted__",
                    request_count=model.request_count,
                    bytes_out=model.bytes_out,
                    client_ip_hash=model.client_ip_hash,
                    worker_id=model.worker_id,
                    worker_url=model.worker_url,
                    egress_ip=model.egress_ip,
                )

    def _persist_session(self, session: Session) -> None:
        if not self.db_ready or SessionLocal is None:
            return
        with SessionLocal() as database:
            model = database.get(RelaySessionModel, session.session_id)
            if model is None:
                database.add(
                    RelaySessionModel(
                        id=session.session_id,
                        cell_id=session.cell_id,
                        origin=session.origin,
                        client_ip_hash=session.client_ip_hash or _ip_hash(session.client_ip),
                        worker_id=session.worker_id,
                        worker_url=session.worker_url,
                        egress_ip=session.egress_ip,
                        created_at=_utc_datetime(session.created_at),
                        expires_at=_utc_datetime(session.expires_at),
                        request_count=session.request_count,
                        bytes_out=session.bytes_out,
                    )
                )
            else:
                model.request_count, model.bytes_out = session.request_count, session.bytes_out
            database.commit()

    def _redis_rate(self, key: str, limit: int, window_seconds: int) -> bool | None:
        if not self.redis_ready or self.redis is None:
            return None
        redis_key = f"relaynorth:rate:{key}:{int(time.time()) // window_seconds}"
        try:
            count = int(self.redis.incr(redis_key))
            if count == 1:
                self.redis.expire(redis_key, window_seconds + 1)
            return count <= limit
        except Exception as exc:
            self.redis_ready, self.redis_error = False, type(exc).__name__
            return None

    def allow_rate(self, key: str, limit: int, window_seconds: int = 60) -> bool:
        distributed = self._redis_rate(key, limit, window_seconds)
        return super().allow_rate(key, limit, window_seconds) if distributed is None else distributed

    def create_session(
        self,
        cell_id: str,
        origin: str,
        ttl: int,
        client_ip: str,
        worker_id: str | None = None,
        worker_url: str | None = None,
        egress_ip: str | None = None,
    ) -> Session:
        session = super().create_session(cell_id, origin, ttl, client_ip, worker_id, worker_url, egress_ip)
        if self.redis_ready and self.redis is not None:
            try:
                self.redis.setex(
                    f"relaynorth:session:{session.session_id}",
                    ttl,
                    json.dumps(
                        {
                            "client_ip_hash": session.client_ip_hash,
                            "origin": origin,
                            "cell_id": cell_id,
                            "worker_id": worker_id,
                            "worker_url": worker_url,
                            "egress_ip": egress_ip,
                        }
                    ),
                )
            except Exception as exc:
                self.redis_ready, self.redis_error = False, type(exc).__name__
        self._persist_session(session)
        return session

    def get_session(self, session_id: str) -> Session | None:
        session = super().get_session(session_id)
        if session is not None or not self.db_ready or SessionLocal is None:
            return session
        with SessionLocal() as database:
            model = database.get(RelaySessionModel, session_id)
            if model is None or (_epoch(model.expires_at) or 0) < int(time.time()):
                return None
            session = Session(
                model.id,
                model.cell_id,
                model.origin,
                _epoch(model.created_at) or int(time.time()),
                _epoch(model.expires_at) or int(time.time()),
                "__persisted__",
                request_count=model.request_count,
                bytes_out=model.bytes_out,
                client_ip_hash=model.client_ip_hash,
                worker_id=model.worker_id,
                worker_url=model.worker_url,
                egress_ip=model.egress_ip,
            )
            self.sessions[session_id] = session
            self.usage["active_sessions"] += 1
            return session

    def bandwidth_used(self, client_ip: str) -> int:
        if self.redis_ready and self.redis is not None:
            try:
                return int(self.redis.get(f"relaynorth:bw:{_ip_hash(client_ip)}") or 0)
            except Exception as exc:
                self.redis_ready, self.redis_error = False, type(exc).__name__
        return super().bandwidth_used(client_ip)

    def begin_request(self, session: Session, client_ip: str, request_limit: int, concurrency_limit: int) -> str | None:
        if session.request_count >= request_limit:
            return "Session request limit reached"
        if (session.client_ip_hash or _ip_hash(session.client_ip)) != _ip_hash(client_ip):
            return "Session client binding mismatch"
        if self.bandwidth_used(client_ip) >= settings.max_bytes_per_ip:
            return "Bandwidth limit reached for this IP"
        if not self.allow_rate(f"proxy:ip:{client_ip}", request_limit):
            return "Request rate limit reached"
        if not self.allow_rate(f"proxy:session:{session.session_id}", request_limit):
            return "Session rate limit reached"
        active_key = f"relaynorth:active:{session.session_id}"
        if self.redis_ready and self.redis is not None:
            try:
                active = int(self.redis.incr(active_key))
                self.redis.expire(active_key, settings.max_session_seconds)
                if active > concurrency_limit:
                    self.redis.decr(active_key)
                    return "Too many concurrent requests"
                return None
            except Exception as exc:
                self.redis_ready, self.redis_error = False, type(exc).__name__
        # The rate buckets above have already been consumed. Only perform the
        # local concurrency check here; delegating to the parent would consume
        # both rate buckets a second time when Redis is unavailable.
        active = self.active_requests.get(session.session_id, 0)
        if active >= concurrency_limit:
            return "Too many concurrent requests"
        self.active_requests[session.session_id] = active + 1
        return None

    def end_request(self, session_id: str) -> None:
        if self.redis_ready and self.redis is not None:
            try:
                if int(self.redis.get(f"relaynorth:active:{session_id}") or 0) > 0:
                    self.redis.decr(f"relaynorth:active:{session_id}")
                return
            except Exception as exc:
                self.redis_ready, self.redis_error = False, type(exc).__name__
        super().end_request(session_id)

    def record_usage(self, session: Session, response_bytes: int) -> None:
        session.request_count += 1
        session.bytes_out += response_bytes
        self.usage["requests"] += 1
        self.usage["bytes_out"] += response_bytes
        ip_key = session.client_ip_hash or _ip_hash(session.client_ip)
        self._add_ip_bytes(ip_key, response_bytes)
        if self.redis_ready and self.redis is not None:
            try:
                self.redis.incrby(f"relaynorth:bw:{ip_key}", response_bytes)
                self.redis.expire(f"relaynorth:bw:{ip_key}", settings.max_session_seconds)
            except Exception as exc:
                self.redis_ready, self.redis_error = False, type(exc).__name__
        self._persist_session(session)
        if self.db_ready and SessionLocal is not None:
            with SessionLocal() as database:
                bucket = int(time.time() // 60) * 60
                aggregate = database.get(UsageAggregateModel, f"usage_{session.cell_id}_{bucket}")
                if aggregate is None:
                    database.add(UsageAggregateModel(id=f"usage_{session.cell_id}_{bucket}", bucket_start=_utc_datetime(bucket), cell_id=session.cell_id, requests=1, bytes_out=response_bytes, blocked=0))
                else:
                    aggregate.requests += 1
                    aggregate.bytes_out += response_bytes
                database.commit()

    def increment_blocked(self) -> None:
        self.usage["blocked"] += 1
        if self.db_ready and SessionLocal is not None:
            with SessionLocal() as database:
                bucket = int(time.time() // 60) * 60
                aggregate = database.get(UsageAggregateModel, f"usage_blocked_{bucket}")
                if aggregate is None:
                    database.add(UsageAggregateModel(id=f"usage_blocked_{bucket}", bucket_start=_utc_datetime(bucket), cell_id="eu", requests=0, bytes_out=0, blocked=1))
                else:
                    aggregate.blocked += 1
                database.commit()

    def add_audit(self, actor: str, action: str, target: str) -> None:
        event = {"id": str(uuid.uuid4()), "actor": actor, "action": action, "target": target, "at": int(time.time())}
        self.audit_events.insert(0, event)
        if self.db_ready and SessionLocal is not None:
            with SessionLocal() as database:
                database.add(AuditEventModel(id=event["id"], actor_id=actor, action=action, target=target, request_metadata={}))
                database.commit()

    def add_abuse_report(self, report: AbuseReport) -> None:
        self.abuse_reports[report.report_id] = report
        if self.db_ready and SessionLocal is not None:
            with SessionLocal() as database:
                database.add(AbuseReportModel(id=report.report_id, category=report.category, description=report.description, status=report.status, created_at=_utc_datetime(report.created_at)))
                database.commit()

    def save_post(self, post: BlogPost, previous_slug: str | None = None) -> None:
        if previous_slug and previous_slug != post.slug:
            self.posts.pop(previous_slug, None)
        self.posts[post.slug] = post
        if not self.db_ready or SessionLocal is None:
            return
        with SessionLocal() as database:
            model = database.get(BlogPostModel, post.post_id)
            author = database.scalar(select(UserModel).where(UserModel.email == post.author.lower())) or database.scalar(select(UserModel).where(UserModel.email == settings.admin_email.lower()))
            category = database.scalar(select(BlogCategoryModel).where(BlogCategoryModel.name == post.category))
            if category is None:
                category = BlogCategoryModel(id=f"category_{uuid.uuid4().hex[:12]}", name=post.category)
                database.add(category)
                database.flush()
            if model is None:
                database.add(self._post_model(post, category.id, author.id))
            else:
                model.slug, model.title, model.excerpt, model.body_markdown = post.slug, post.title, post.excerpt, post.body_markdown
                model.status, model.category_id, model.tags = post.status, category.id, post.tags
                model.seo_title, model.seo_description, model.updated_at = post.seo_title, post.seo_description, _utc_datetime(post.updated_at)
                model.published_at = _utc_datetime(post.published_at) if post.published_at else None
            database.flush()
            for revision in post.revisions:
                if database.get(BlogRevisionModel, revision["revision_id"]) is None:
                    database.add(self._revision_model(post, revision, author.id))
            database.commit()

    def set_region_state(self, cell_id: str, state: str, drain_deadline: int | None) -> Region | None:
        region = self.regions.get(cell_id)
        if region is None:
            return None
        region.health_state, region.drain_deadline = state, drain_deadline
        if self.db_ready and SessionLocal is not None:
            with SessionLocal() as database:
                model = database.get(RegionModel, cell_id)
                if model is not None:
                    model.health_state, model.drain_deadline = state, _utc_datetime(drain_deadline) if drain_deadline else None
                    database.commit()
        return region


store = PersistentStore()
