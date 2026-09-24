from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def sign_payload(payload: dict[str, Any], secret: str) -> str:
    encoded = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    signature = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).digest()
    return f"{encoded}.{_b64(signature)}"


def verify_payload(token: str, secret: str) -> dict[str, Any] | None:
    try:
        encoded, signature = token.split(".", 1)
        expected = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(_unb64(signature), expected):
            return None
        payload = json.loads(_unb64(encoded))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return payload
    except (ValueError, TypeError, json.JSONDecodeError, base64.binascii.Error):
        return None


def password_digest(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()
