from __future__ import annotations

import os
from dataclasses import dataclass, field


def _csv(name: str, default: str) -> tuple[str, ...]:
    raw = os.getenv(name, default)
    return tuple(item.strip().rstrip("/") for item in raw.split(",") if item.strip())


@dataclass(frozen=True)
class Settings:
    app_env: str = os.getenv("APP_ENV", "development")
    secret_key: str = os.getenv("SECRET_KEY", "dev-only-change-me")
    admin_email: str = os.getenv("ADMIN_EMAIL", "owner@relaynorth.local")
    admin_password: str = os.getenv("ADMIN_PASSWORD", "change-me-now")
    # These defaults match docker-compose.yml's host-facing development ports.
    # A deployment should always provide explicit secrets/URLs through its environment.
    database_url: str | None = os.getenv(
        "DATABASE_URL", "postgresql+psycopg://relaynorth:relaynorth@127.0.0.1:55432/relaynorth"
    )
    redis_url: str | None = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    allowed_proxy_origins: tuple[str, ...] = _csv(
        "ALLOWED_PROXY_ORIGINS", "http://127.0.0.1:9001,http://localhost:9001"
    )
    allow_local_test_origin: bool = os.getenv("ALLOW_LOCAL_TEST_ORIGIN", "true").lower() == "true"
    # Admin cookie SameSite. Default "lax" for same-origin; set "none" (with
    # APP_ENV=production so the cookie is Secure) when the admin console is hosted
    # on a different origin than this API.
    admin_cookie_samesite: str = os.getenv("ADMIN_COOKIE_SAMESITE", "lax")
    require_regional_workers: bool = os.getenv("REQUIRE_REGIONAL_WORKERS", "false").lower() == "true"
    worker_shared_secret: str = os.getenv("WORKER_SHARED_SECRET", "")
    worker_urls: dict[str, str] = field(
        default_factory=lambda: {
            "eu": os.getenv("RELAY_WORKER_EU_URL", "").rstrip("/"),
            "us": os.getenv("RELAY_WORKER_US_URL", "").rstrip("/"),
            "apac": os.getenv("RELAY_WORKER_APAC_URL", "").rstrip("/"),
        }
    )
    worker_ids: dict[str, str] = field(
        default_factory=lambda: {
            "eu": os.getenv("RELAY_WORKER_EU_ID", "eu-worker-01"),
            "us": os.getenv("RELAY_WORKER_US_ID", "us-worker-01"),
            "apac": os.getenv("RELAY_WORKER_APAC_ID", "apac-worker-01"),
        }
    )
    worker_egress_ips: dict[str, str] = field(
        default_factory=lambda: {
            "eu": os.getenv("RELAY_EGRESS_IP_EU", "203.0.113.10"),
            "us": os.getenv("RELAY_EGRESS_IP_US", "203.0.113.20"),
            "apac": os.getenv("RELAY_EGRESS_IP_APAC", "203.0.113.30"),
        }
    )
    max_url_length: int = 2048
    max_response_bytes: int = 2_000_000
    max_session_seconds: int = 900
    session_create_rate_limit: int = 10
    proxy_request_rate_limit: int = 60
    max_session_requests: int = 120
    max_concurrent_requests: int = 2
    # Cumulative egress a single client IP may pull through the relay before it
    # is throttled (100 MB). Guards the shared test relay from one noisy source.
    max_bytes_per_ip: int = 100 * 1024 * 1024

    def worker_for_region(self, cell_id: str) -> tuple[str, str, str] | None:
        url = self.worker_urls.get(cell_id, "")
        if not url:
            return None
        return self.worker_ids.get(cell_id, f"{cell_id}-worker-01"), url, self.worker_egress_ips.get(cell_id, "")


settings = Settings()
