from __future__ import annotations

import asyncio
import base64
import ipaddress
import re
from urllib.parse import quote, urljoin, urlsplit

import httpx
from fastapi import HTTPException

from .config import settings
from .store import Session, store


UNSAFE_SCHEMES = {"file", "data", "javascript", "gopher", "ftp", "smb", "dict"}
PRIVATE_HOSTS = {"localhost", "metadata.google.internal", "metadata.amazonaws.com"}


def _is_local_allowed(host: str) -> bool:
    return settings.allow_local_test_origin and host in {"127.0.0.1", "localhost"}


def _is_private_ip(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
        return address.is_private or address.is_loopback or address.is_link_local or address.is_multicast or address.is_unspecified
    except ValueError:
        return False


def validate_url(value: str, allowed_origins: tuple[str, ...] | None = None) -> str:
    if not value or len(value) > settings.max_url_length:
        raise HTTPException(status_code=400, detail="URL is missing or too long")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        raise HTTPException(status_code=400, detail="Only credential-free HTTP(S) URLs are accepted")
    try:
        port = parsed.port
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Destination port is invalid") from exc
    local_fixture_port = port == 9001 and parsed.hostname and _is_local_allowed(parsed.hostname.lower())
    if port not in {None, 80, 443} and not local_fixture_port:
        raise HTTPException(status_code=403, detail="Destination port is not allowed")
    if not parsed.hostname:
        raise HTTPException(status_code=400, detail="Destination hostname is required")
    if parsed.hostname.lower() in PRIVATE_HOSTS or _is_private_ip(parsed.hostname):
        if not _is_local_allowed(parsed.hostname.lower()):
            raise HTTPException(status_code=403, detail="Private and metadata destinations are blocked")
    allowed = settings.allowed_proxy_origins if allowed_origins is None else allowed_origins
    origin = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    if not any(origin == allowed_origin or origin.startswith(f"{allowed_origin}/") for allowed_origin in allowed):
        raise HTTPException(status_code=403, detail="Destination is not on the approved origin list")
    return value


def _rewrite_resource(match: re.Match[str], session_id: str, base_url: str) -> str:
    attribute, raw_url = match.group(1), match.group(2)
    if raw_url.startswith(("#", "data:", "mailto:", "javascript:")):
        return match.group(0)
    target = urljoin(base_url, raw_url)
    return f'{attribute}="/api/v1/proxy/{session_id}?url={quote(target, safe="")}"'


def rewrite_html(body: str, session_id: str, base_url: str) -> str:
    pattern = re.compile(r'((?:href|src|action))=["\']([^"\']+)["\']', re.IGNORECASE)
    return pattern.sub(lambda match: _rewrite_resource(match, session_id, base_url), body)


async def fetch_for_session(
    session: Session,
    target_url: str,
    method: str = "GET",
    body: bytes | None = None,
    content_type: str | None = None,
) -> tuple[int, dict[str, str], bytes]:
    if session.worker_url:
        return await fetch_via_worker(session, target_url, method, body, content_type)
    validate_url(target_url, (session.origin,))
    async with httpx.AsyncClient(follow_redirects=False, timeout=httpx.Timeout(12.0, connect=4.0)) as client:
        try:
            headers = {"User-Agent": "RelayNorth-TestRelay/0.1"}
            if content_type:
                headers["Content-Type"] = content_type
            response = await client.request(method, target_url, headers=headers, content=body, cookies=session.cookies)
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail="The approved test origin could not be reached") from exc
    content = response.content
    if len(content) > settings.max_response_bytes:
        raise HTTPException(status_code=413, detail="Response exceeds the configured limit")
    for key, value in response.headers.items():
        if key.lower() == "set-cookie":
            cookie_name, _, cookie_value = value.partition("=")
            session.cookies[cookie_name] = cookie_value.split(";", 1)[0]
    location = response.headers.get("location")
    headers = {"content-type": response.headers.get("content-type", "application/octet-stream")}
    if location:
        next_url = urljoin(target_url, location)
        validate_url(next_url, (session.origin,))
        headers["location"] = f"/api/v1/proxy/{session.session_id}?url={quote(next_url, safe='')}"
    if "text/html" in headers["content-type"]:
        content = rewrite_html(content.decode("utf-8", errors="replace"), session.session_id, target_url).encode()
    store.record_usage(session, len(content))
    await asyncio.sleep(0)
    return response.status_code, headers, content


async def fetch_via_worker(
    session: Session,
    target_url: str,
    method: str = "GET",
    body: bytes | None = None,
    content_type: str | None = None,
) -> tuple[int, dict[str, str], bytes]:
    """Fetch through the worker pinned to the session, then apply control-plane rewriting."""
    validate_url(target_url, (session.origin,))
    if not settings.worker_shared_secret:
        raise HTTPException(status_code=503, detail="Regional worker authentication is not configured")
    payload = {
        "session_id": session.session_id,
        "target_url": target_url,
        "method": method,
        "body_b64": base64.b64encode(body or b"").decode("ascii"),
        "content_type": content_type,
        "cookies": session.cookies,
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=4.0)) as client:
            response = await client.post(
                f"{session.worker_url.rstrip('/')}/internal/v1/fetch",
                headers={"Authorization": f"Bearer {settings.worker_shared_secret}"},
                json=payload,
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="The selected relay worker could not be reached") from exc
    if response.status_code >= 400:
        detail = response.json().get("detail", "The selected relay worker rejected the request")
        raise HTTPException(status_code=response.status_code, detail=detail)
    try:
        result = response.json()
        content = base64.b64decode(result["body_b64"])
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=502, detail="The selected relay worker returned an invalid response") from exc
    if len(content) > settings.max_response_bytes:
        raise HTTPException(status_code=413, detail="Response exceeds the configured limit")
    for cookie in result.get("set_cookies", []):
        cookie_name, _, cookie_value = cookie.partition("=")
        if cookie_name:
            session.cookies[cookie_name] = cookie_value.split(";", 1)[0]
    location = result.get("location")
    headers = {"content-type": result.get("content_type", "application/octet-stream")}
    if location:
        next_url = urljoin(target_url, location)
        validate_url(next_url, (session.origin,))
        headers["location"] = f"/api/v1/proxy/{session.session_id}?url={quote(next_url, safe='')}"
    if "text/html" in headers["content-type"]:
        content = rewrite_html(content.decode("utf-8", errors="replace"), session.session_id, target_url).encode()
    store.record_usage(session, len(content))
    return int(result.get("status_code", 502)), headers, content
