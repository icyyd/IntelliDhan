"""Owner-session authentication and lightweight abuse controls."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, WebSocket


OWNER_COOKIE = "intellidhan_owner"
SESSION_MAX_AGE_SECONDS = 60 * 60 * 12
SESSION_CLOCK_SKEW_SECONDS = 60


def owner_token() -> str | None:
    value = os.getenv("INTELLIDHAN_OWNER_TOKEN")
    return value if value and len(value) >= 24 else None


def owner_configured() -> bool:
    return owner_token() is not None


def _cookie_value(token: str, issued_at: int | None = None) -> str:
    issued_at = int(time.time()) if issued_at is None else int(issued_at)
    message = f"intellidhan-owner-session-v2:{issued_at}".encode()
    signature = hmac.new(
        token.encode(), message, hashlib.sha256
    ).hexdigest()
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
