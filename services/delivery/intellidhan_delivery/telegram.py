"""Telegram sender — env-gated, owner-chat-locked, console fallback (docs 02 §5, 13).

Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to go live; otherwise messages
print to stdout so the pipeline is exercisable end-to-end without credentials.
Delivery is at-least-once: callers dedupe by alert_id (doc 01 §2④ outbox).
"""

from __future__ import annotations

import os

import httpx


class TelegramSender:
    def __init__(self) -> None:
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        self.live = bool(self.token and self.chat_id)

    async def send(self, text: str) -> bool:
        if not self.live:
            print(f"[telegram:console-fallback]\n{text}\n")
            return True
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json={"chat_id": self.chat_id, "text": text})
        return resp.status_code == 200
