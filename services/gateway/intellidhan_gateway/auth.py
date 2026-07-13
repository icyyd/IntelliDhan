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


def owner_token() -> str | None:
    value = os.getenv("INTELLIDHAN_OWNER_TOKEN")
    return value if value and len(value) >= 24 else None


def owner_configured() -> bool:
    return owner_token() is not None


def _cookie_value(token: str) -> str:
    signature = hmac.new(
        token.encode(), b"intellidhan-owner-session-v1", hashlib.sha256
    ).hexdigest()
    return f"v1.{signature}"


def verify_owner_token(candidate: str) -> bool:
    expected = owner_token()
    return bool(expected and candidate and secrets.compare_digest(candidate, expected))


def request_is_owner(request: Request) -> bool:
    expected = owner_token()
    if expected is None:
        return False
    cookie = request.cookies.get(OWNER_COOKIE, "")
    if cookie and secrets.compare_digest(cookie, _cookie_value(expected)):
        return True
    auth = request.headers.get("authorization", "")
    candidate = auth[7:] if auth.lower().startswith("bearer ") else ""
    return bool(candidate and secrets.compare_digest(candidate, expected))


def websocket_is_owner(websocket: WebSocket) -> bool:
    expected = owner_token()
    if expected is None:
        return False
    cookie = websocket.cookies.get(OWNER_COOKIE, "")
    return bool(cookie and secrets.compare_digest(cookie, _cookie_value(expected)))


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
