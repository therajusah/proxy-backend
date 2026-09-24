from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class RoleModel(Base):
    __tablename__ = "roles"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(40), unique=True, index=True)


class UserModel(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role_id: Mapped[str] = mapped_column(ForeignKey("roles.id"), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RegionModel(Base):
    __tablename__ = "regions"

    cell_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(80))
    country: Mapped[str] = mapped_column(String(80))
    health_state: Mapped[str] = mapped_column(String(20), default="ready", index=True)
    capacity: Mapped[int] = mapped_column(Integer, default=72)
    egress_ip_pool: Mapped[list[str]] = mapped_column(JSON, default=list)
    drain_deadline: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    policy_version: Mapped[int] = mapped_column(Integer, default=1)


class RelaySessionModel(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    cell_id: Mapped[str] = mapped_column(ForeignKey("regions.cell_id"), index=True)
    origin: Mapped[str] = mapped_column(String(512))
    client_ip_hash: Mapped[str] = mapped_column(String(128))
    worker_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    worker_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    egress_ip: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    request_count: Mapped[int] = mapped_column(Integer, default=0)
    bytes_out: Mapped[int] = mapped_column(Integer, default=0)


class BlogCategoryModel(Base):
    __tablename__ = "blog_categories"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)


class BlogPostModel(Base):
    __tablename__ = "blog_posts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    slug: Mapped[str] = mapped_column(String(180), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(140))
    excerpt: Mapped[str] = mapped_column(String(280))
    body_markdown: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), index=True)
    category_id: Mapped[str] = mapped_column(ForeignKey("blog_categories.id"), index=True)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    seo_title: Mapped[str] = mapped_column(String(180))
    seo_description: Mapped[str] = mapped_column(String(320))
    author_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class BlogRevisionModel(Base):
    __tablename__ = "blog_revisions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    post_id: Mapped[str] = mapped_column(ForeignKey("blog_posts.id"), index=True)
    body_markdown: Mapped[str] = mapped_column(Text)
    editor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class BlogTagModel(Base):
    __tablename__ = "blog_tags"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)


class AbuseReportModel(Base):
    __tablename__ = "abuse_reports"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    category: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class BlockedDestinationModel(Base):
    __tablename__ = "blocked_destinations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    origin: Mapped[str] = mapped_column(String(512), unique=True, index=True)
    reason: Mapped[str] = mapped_column(String(500))
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class UsageAggregateModel(Base):
    __tablename__ = "usage_aggregates"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    bucket_start: Mapped[datetime] = mapped_column(DateTime, index=True)
    cell_id: Mapped[str] = mapped_column(ForeignKey("regions.cell_id"), index=True)
    requests: Mapped[int] = mapped_column(Integer, default=0)
    bytes_out: Mapped[int] = mapped_column(Integer, default=0)
    blocked: Mapped[int] = mapped_column(Integer, default=0)


class AuditEventModel(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(320))
    action: Mapped[str] = mapped_column(String(120), index=True)
    target: Mapped[str] = mapped_column(String(512))
    request_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
