from __future__ import annotations

import time
from typing import Protocol


class StateBackend(Protocol):
    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str, ttl_seconds: int | None = None) -> None: ...
    async def increment(self, key: str, ttl_seconds: int | None = None) -> int: ...
    async def delete(self, key: str) -> None: ...


class MemoryState:
    """Development fallback with Redis-like TTL semantics."""

    def __init__(self) -> None:
        self._values: dict[str, tuple[str, float | None]] = {}

    def _active(self, key: str) -> str | None:
        item = self._values.get(key)
        if not item:
            return None
        value, expires_at = item
        if expires_at is not None and expires_at <= time.time():
            self._values.pop(key, None)
            return None
        return value

    async def get(self, key: str) -> str | None:
        return self._active(key)

    async def set(self, key: str, value: str, ttl_seconds: int | None = None) -> None:
        expires_at = time.time() + ttl_seconds if ttl_seconds else None
        self._values[key] = (value, expires_at)

    async def increment(self, key: str, ttl_seconds: int | None = None) -> int:
        current = int(self._active(key) or "0") + 1
        await self.set(key, str(current), ttl_seconds)
        return current

    async def delete(self, key: str) -> None:
        self._values.pop(key, None)


class RedisState:
    def __init__(self, url: str) -> None:
        try:
            from redis.asyncio import Redis
        except ImportError as exc:  # pragma: no cover - dependency is installed in deploys
            raise RuntimeError("Install redis to use RedisState") from exc
        self.client = Redis.from_url(url, decode_responses=True)

    async def get(self, key: str) -> str | None:
        return await self.client.get(key)

    async def set(self, key: str, value: str, ttl_seconds: int | None = None) -> None:
        await self.client.set(key, value, ex=ttl_seconds)

    async def increment(self, key: str, ttl_seconds: int | None = None) -> int:
        value = await self.client.incr(key)
        if value == 1 and ttl_seconds:
            await self.client.expire(key, ttl_seconds)
        return value

    async def delete(self, key: str) -> None:
        await self.client.delete(key)
