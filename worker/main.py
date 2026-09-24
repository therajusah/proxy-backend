from __future__ import annotations

import base64
import hmac
import os
from typing import Literal

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from backend.app.config import settings
from backend.app.proxy import validate_url


WORKER_ID = os.getenv("WORKER_ID", "regional-worker")
WORKER_REGION = os.getenv("WORKER_REGION", "eu")
WORKER_EGRESS_IP = os.getenv("WORKER_EGRESS_IP", "")
WORKER_BIND_IP = os.getenv("WORKER_BIND_IP", "")
WORKER_SHARED_SECRET = os.getenv("WORKER_SHARED_SECRET", settings.worker_shared_secret)

app = FastAPI(title=f"RelayNorth {WORKER_REGION} relay worker", version="0.1.0")


class FetchInput(BaseModel):
    session_id: str = Field(min_length=8, max_length=80)
    target_url: str = Field(min_length=8, max_length=2048)
    method: Literal["GET", "POST"] = "GET"
    body_b64: str = ""
    content_type: str | None = None
    cookies: dict[str, str] = Field(default_factory=dict)


def require_worker_auth(authorization: str | None = Header(default=None)) -> None:
    expected = f"Bearer {WORKER_SHARED_SECRET}"
    if not WORKER_SHARED_SECRET or not authorization or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="Worker authentication required")


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "worker_id": WORKER_ID, "region": WORKER_REGION, "egress_ip": WORKER_EGRESS_IP}


@app.post("/internal/v1/heartbeat", dependencies=[Depends(require_worker_auth)])
def heartbeat() -> dict:
    return {"worker_id": WORKER_ID, "region": WORKER_REGION, "egress_ip": WORKER_EGRESS_IP, "status": "ready"}


@app.get("/internal/v1/egress-check", dependencies=[Depends(require_worker_auth)])
def egress_check() -> dict:
    return {"worker_id": WORKER_ID, "region": WORKER_REGION, "egress_ip": WORKER_EGRESS_IP}


@app.post("/internal/v1/fetch", dependencies=[Depends(require_worker_auth)])
async def fetch(payload: FetchInput) -> dict:
    validate_url(payload.target_url)
    if payload.method == "POST" and payload.content_type != "application/x-www-form-urlencoded":
        raise HTTPException(status_code=415, detail="Only form-urlencoded POST requests are supported")
    try:
        body = base64.b64decode(payload.body_b64) if payload.body_b64 else None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid request body encoding") from exc
    if body is not None and len(body) > 32_000:
        raise HTTPException(status_code=413, detail="Form body exceeds the configured limit")
    headers = {"User-Agent": "RelayNorth-RegionalWorker/0.1"}
    if payload.content_type:
        headers["Content-Type"] = payload.content_type
    try:
        transport = httpx.AsyncHTTPTransport(local_address=WORKER_BIND_IP or None)
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=httpx.Timeout(12.0, connect=4.0),
            transport=transport,
        ) as client:
            response = await client.request(payload.method, payload.target_url, headers=headers, content=body, cookies=payload.cookies)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="The regional worker could not reach the approved origin") from exc
    if len(response.content) > settings.max_response_bytes:
        raise HTTPException(status_code=413, detail="Response exceeds the configured limit")
    return {
        "status_code": response.status_code,
        "content_type": response.headers.get("content-type", "application/octet-stream"),
        "location": response.headers.get("location"),
        "set_cookies": response.headers.get_list("set-cookie"),
        "body_b64": base64.b64encode(response.content).decode("ascii"),
    }
