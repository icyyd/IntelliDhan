"""Database-backed account sessions plus legacy owner-token compatibility."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException, Request, WebSocket


OWNER_COOKIE = "intellidhan_owner"
SESSION_COOKIE = "intellidhan_session"
# Keep browser and database-backed account sessions aligned.  This is an
# absolute lifetime (not an idle timeout), so a user who signs in can return
# for 30 days without being silently logged out.  Explicit logout/revocation
# still invalidates the session immediately.
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 30
SESSION_CLOCK_SKEW_SECONDS = 60
PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 128
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1


@dataclass(frozen=True)
class Principal:
    user_id: str
    email: str
    display_name: str
    role: str
    expires_at: int
    legacy: bool = False

    def public(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "email": self.email,
            "display_name": self.display_name,
            "role": self.role,
            "legacy": self.legacy,
        }


def owner_token() -> str | None:
    value = os.getenv("INTELLIDHAN_OWNER_TOKEN")
    return value if value and len(value) >= 24 else None


def owner_configured() -> bool:
    return owner_token() is not None


def invite_code() -> str | None:
    value = os.getenv("INTELLIDHAN_INVITE_CODE")
    return value if value and len(value) >= 12 else None


def invite_configured() -> bool:
    return invite_code() is not None


def verify_invite_code(candidate: str, *, first_account: bool) -> bool:
    if first_account:
        return verify_owner_token(candidate)
    expected = invite_code()
    return bool(expected and candidate and secrets.compare_digest(candidate, expected))


def _cookie_value(token: str, issued_at: int | None = None) -> str:
    issued_at = int(time.time()) if issued_at is None else int(issued_at)
    message = f"intellidhan-owner-session-v2:{issued_at}".encode()
    signature = hmac.new(token.encode(), message, hashlib.sha256).hexdigest()
    return f"v2.{issued_at}.{signature}"


def _cookie_expires_at(cookie: str, token: str, now: int | None = None) -> int | None:
    try:
        version, issued_raw, _ = cookie.split(".", 2)
        issued_at = int(issued_raw)
    except (TypeError, ValueError):
        return None
    if version != "v2":
        return None
    now = int(time.time()) if now is None else int(now)
    age = now - issued_at
    if age < -SESSION_CLOCK_SKEW_SECONDS or age > SESSION_MAX_AGE_SECONDS:
        return None
    if not secrets.compare_digest(cookie, _cookie_value(token, issued_at)):
        return None
    return issued_at + SESSION_MAX_AGE_SECONDS


def _valid_cookie(cookie: str, token: str, now: int | None = None) -> bool:
    return _cookie_expires_at(cookie, token, now) is not None


def verify_owner_token(candidate: str) -> bool:
    expected = owner_token()
    return bool(expected and candidate and secrets.compare_digest(candidate, expected))


def request_is_owner(request: Request) -> bool:
    expected = owner_token()
    if expected is None:
        return False
    cookie = request.cookies.get(OWNER_COOKIE, "")
    if cookie and _valid_cookie(cookie, expected):
        return True
    auth = request.headers.get("authorization", "")
    candidate = auth[7:] if auth.lower().startswith("bearer ") else ""
    return bool(candidate and secrets.compare_digest(candidate, expected))


def websocket_is_owner(websocket: WebSocket) -> bool:
    return websocket_owner_expires_at(websocket) is not None


def websocket_owner_expires_at(websocket: WebSocket) -> int | None:
    expected = owner_token()
    if expected is None:
        return None
    cookie = websocket.cookies.get(OWNER_COOKIE, "")
    return _cookie_expires_at(cookie, expected) if cookie else None


def require_owner(request: Request) -> None:
    if not owner_configured():
        raise HTTPException(
            status_code=503,
            detail="owner authentication is not configured",
        )
    if not request_is_owner(request):
        raise HTTPException(status_code=401, detail="owner sign-in required")


def session_cookie_value() -> str:
    token = owner_token()
    if token is None:
        raise RuntimeError("owner authentication is not configured")
    return _cookie_value(token)


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def validate_password(password: str) -> None:
    if len(password) < PASSWORD_MIN_LENGTH or len(password) > PASSWORD_MAX_LENGTH:
        raise ValueError(
            f"password must be {PASSWORD_MIN_LENGTH}–{PASSWORD_MAX_LENGTH} characters"
        )


def hash_password(password: str) -> str:
    validate_password(password)
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64encode(salt)}${_b64encode(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt, expected = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        n_value, r_value, p_value = int(n), int(r), int(p)
        if (n_value, r_value, p_value) != (_SCRYPT_N, _SCRYPT_R, _SCRYPT_P):
            return False
        expected_bytes = _b64decode(expected)
        actual = hashlib.scrypt(
            password.encode(),
            salt=_b64decode(salt),
            n=n_value,
            r=r_value,
            p=p_value,
            dklen=len(expected_bytes),
        )
        return secrets.compare_digest(actual, expected_bytes)
    except (TypeError, ValueError, binascii.Error):
        return False


def session_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_account_session(store: Any, user_id: str) -> tuple[str, int]:
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(seconds=SESSION_MAX_AGE_SECONDS)
    store.create_user_session(
        session_hash=session_token_hash(token),
        user_id=user_id,
        expires_at=expires.isoformat(),
    )
    return token, int(expires.timestamp())


def _timestamp(value: str) -> int:
    return int(datetime.fromisoformat(value).timestamp())


def _account_principal(token: str, store: Any) -> Principal | None:
    if not token:
        return None
    record = store.get_user_session(session_token_hash(token))
    if not record:
        return None
    return Principal(
        user_id=record["user_id"],
        email=record["email"],
        display_name=record["display_name"],
        role=record["role"],
        expires_at=_timestamp(record["expires_at"]),
    )


def request_principal(request: Request, store: Any) -> Principal | None:
    principal = _account_principal(request.cookies.get(SESSION_COOKIE, ""), store)
    if principal:
        return principal
    if request_is_owner(request):
        expected = owner_token()
        cookie = request.cookies.get(OWNER_COOKIE, "")
        expires_at = (
            _cookie_expires_at(cookie, expected) if expected and cookie else None
        ) or int(time.time()) + SESSION_MAX_AGE_SECONDS
        return Principal(
            user_id="legacy-owner",
            email="",
            display_name="Administrator",
            role="ADMIN",
            expires_at=expires_at,
            legacy=True,
        )
    return None


def websocket_principal(websocket: WebSocket, store: Any) -> Principal | None:
    principal = _account_principal(websocket.cookies.get(SESSION_COOKIE, ""), store)
    if principal:
        return principal
    expires_at = websocket_owner_expires_at(websocket)
    if expires_at is None:
        return None
    return Principal(
        user_id="legacy-owner",
        email="",
        display_name="Administrator",
        role="ADMIN",
        expires_at=expires_at,
        legacy=True,
    )


def require_user(
    request: Request, store: Any, *, roles: set[str] | None = None
) -> Principal:
    principal = request_principal(request, store)
    if principal is None:
        raise HTTPException(status_code=401, detail="sign in required")
    if roles and principal.role not in roles:
        raise HTTPException(status_code=403, detail="this account cannot perform that action")
    return principal


class RateLimiter:
    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, *, limit: int, window_seconds: int) -> None:
        now = time.monotonic()
        events = self._events[key]
        cutoff = now - window_seconds
        while events and events[0] <= cutoff:
            events.popleft()
        if len(events) >= limit:
            raise HTTPException(status_code=429, detail="rate limit exceeded; retry shortly")
        events.append(now)
