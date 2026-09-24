import pytest
from fastapi import HTTPException

from backend.app.config import Settings
from backend.app.proxy import validate_url
from backend.app.security import sign_payload, verify_payload


def local_settings() -> Settings:
    return Settings(
        secret_key="test-secret",
        allowed_proxy_origins=("http://127.0.0.1:9001",),
        allow_local_test_origin=True,
    )


def test_allowed_owned_origin_is_valid() -> None:
    assert validate_url("http://127.0.0.1:9001/", local_settings().allowed_proxy_origins).startswith(
        "http://127.0.0.1:9001"
    )


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "gopher://127.0.0.1:70/",
        "http://user:password@127.0.0.1:9001/",
        "http://127.0.0.1:9001:8080/",
    ],
)
def test_unsafe_url_shapes_are_rejected(url: str) -> None:
    with pytest.raises(HTTPException):
        validate_url(url, local_settings().allowed_proxy_origins)


@pytest.mark.parametrize("host", ["169.254.169.254", "10.0.0.1", "::1"])
def test_private_and_metadata_addresses_are_rejected(host: str) -> None:
    url = f"http://[{host}]/" if ":" in host else f"http://{host}/"
    with pytest.raises(HTTPException):
        validate_url(url, Settings(allowed_proxy_origins=()))


def test_unapproved_origin_is_rejected() -> None:
    with pytest.raises(HTTPException):
        validate_url("https://example.com/", local_settings().allowed_proxy_origins)


def test_signed_session_token_detects_tampering() -> None:
    token = sign_payload({"sid": "session-1", "cell": "eu", "exp": 9_999_999_999}, "secret")
    assert verify_payload(token, "secret")["sid"] == "session-1"
    assert verify_payload(token + "tampered", "secret") is None
