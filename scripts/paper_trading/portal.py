"""Read-only, same-origin loopback journal with HttpOnly operator sessions."""
from __future__ import annotations

import json
import secrets
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


class PaperPortal(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, runner, web_dir: Path):
        if address[0] != "127.0.0.1":
            raise ValueError("Paper journal may bind only to 127.0.0.1")
        self.runner = runner
        self.web_dir = web_dir
        self.sessions: dict[str, float] = {}
        super().__init__(address, JournalHandler)


class JournalHandler(BaseHTTPRequestHandler):
    server: PaperPortal

    def log_message(self, *_args):
        pass  # Never log cookies or request URLs.

    def allowed(self) -> bool:
        host = f"127.0.0.1:{self.server.server_port}"
        return (
            self.client_address[0] == "127.0.0.1"
            and self.headers.get("Host") == host
            and self.headers.get("Sec-Fetch-Site", "none") in {"none", "same-origin"}
            and self.headers.get("Origin", f"http://{host}") == f"http://{host}"
        )

    def authenticated(self) -> bool:
        try:
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            token = cookie["paper_session"].value
            return self.server.sessions.get(token, 0) > time.time()
        except (KeyError, ValueError):
            return False

    def respond(self, status: int, body: bytes, content_type="application/json", cookie=None, download=False):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        if cookie:
            self.send_header("Set-Cookie", f"paper_session={cookie}; HttpOnly; SameSite=Strict; Path=/; Max-Age=86400")
        if download:
            self.send_header("Content-Disposition", 'attachment; filename="intellidhan-paper-journal.json"')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.allowed():
            self.respond(403, b'{"error":"Local same-origin access only"}')
            return
        path = urlsplit(self.path).path
        if path == "/api/paper/health":
            # Non-personal liveness only; never authorizes a paper entry.
            self.respond(200, b'{"mode":"SIMULATION","portal":"UP"}')
            return
        if path in {"/", "/paper"}:
            if self.headers.get("Sec-Fetch-Dest", "document") not in {"document", "empty"}:
                self.respond(403, b'{"error":"Open the journal in its own tab"}')
                return
            token = secrets.token_urlsafe(32)
            now = time.time()
            self.server.sessions = {k: v for k, v in self.server.sessions.items() if v > now}
            if len(self.server.sessions) >= 100:
                self.respond(429, b'{"error":"Too many local browser sessions"}')
                return
            self.server.sessions[token] = now + 86400
            self.respond(200, (self.server.web_dir / "paper.html").read_bytes(), "text/html; charset=utf-8", cookie=token)
            return
        if not self.authenticated():
            self.respond(401, b'{"error":"Open the local journal to establish a browser session"}')
            return
        if path in {"/api/paper/state", "/api/paper/export.json"}:
            self.respond(200, json.dumps(self.server.runner.snapshot(full_history=path.endswith(".json")), allow_nan=False).encode(), download=path.endswith(".json"))
            return
        assets = {
            "/assets/paper.css": "text/css; charset=utf-8",
            "/assets/paper.js": "application/javascript; charset=utf-8",
            "/assets/brand/intellidhan-mark.svg": "image/svg+xml",
            "/assets/brand/intellidhan-mark-inverse.svg": "image/svg+xml",
            "/assets/brand/intellidhan-lockup.svg": "image/svg+xml",
            "/assets/brand/favicon.svg": "image/svg+xml",
        }
        if path in assets:
            self.respond(200, (self.server.web_dir / path.lstrip("/")).read_bytes(), assets[path])
        else:
            self.respond(404, b'{"error":"Not found"}')

    def do_POST(self):
        self.respond(405, b'{"error":"This journal is read-only"}')

    do_PUT = do_POST
    do_DELETE = do_POST
    do_PATCH = do_POST
