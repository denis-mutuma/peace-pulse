from __future__ import annotations

import hashlib
import hmac
import secrets
import base64
import struct
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from passlib.hash import pbkdf2_sha256

from .config import get_settings


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


def hash_password(password: str) -> str:
    return pbkdf2_sha256.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return pbkdf2_sha256.verify(password, hashed)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def make_token_urlsafe() -> str:
    return secrets.token_urlsafe(32)


def make_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _totp_at(secret: str, step: int) -> str:
    padded = secret.upper() + "=" * ((8 - len(secret) % 8) % 8)
    key = base64.b32decode(padded)
    msg = struct.pack(">Q", step)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{code % 1_000_000:06d}"


def verify_totp(secret: str, code: str, now: int | None = None) -> bool:
    if not code or not code.isdigit():
        return False
    current = int((now or time.time()) // 30)
    return any(hmac.compare_digest(_totp_at(secret, current + drift), code) for drift in (-1, 0, 1))


def protect_secret(secret: str) -> str:
    settings = get_settings()
    key = hashlib.sha256(settings.jwt_secret.encode("utf-8")).digest()
    data = secret.encode("utf-8")
    encrypted = bytes(byte ^ key[index % len(key)] for index, byte in enumerate(data))
    return "enc:" + base64.urlsafe_b64encode(encrypted).decode("ascii")


def reveal_secret(protected: str) -> str:
    if not protected.startswith("enc:"):
        return ""
    settings = get_settings()
    key = hashlib.sha256(settings.jwt_secret.encode("utf-8")).digest()
    data = base64.urlsafe_b64decode(protected[4:].encode("ascii"))
    return bytes(byte ^ key[index % len(key)] for index, byte in enumerate(data)).decode("utf-8")


def create_access_token(subject: str, org_id: str, site_ids: list[str], roles: list[str]) -> str:
    settings = get_settings()
    issued = now_utc()
    payload: dict[str, Any] = {
        "iss": settings.jwt_issuer,
        "sub": subject,
        "org_id": org_id,
        "site_ids": site_ids,
        "roles": roles,
        "iat": int(issued.timestamp()),
        "exp": int((issued + timedelta(minutes=settings.access_token_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_access_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"], issuer=settings.jwt_issuer)


def sign_hub_payload(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def verify_hub_signature(secret: str, body: bytes, signature: str) -> bool:
    expected = sign_hub_payload(secret, body)
    return hmac.compare_digest(expected, signature)
