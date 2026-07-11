"""Probabilistic-voice linter (doc 17 §1) — enforced at compose time.

Alerts must never predict. Banned words fail composition loudly; the
complement line is injected automatically so no alert ships without it.
"""

from __future__ import annotations

import re

BANNED = re.compile(
    r"\b(will\s+(rise|fall|go|moon|rally|drop|crash)|guaranteed|sure\s+thing|"
    r"can't\s+lose|cannot\s+lose|certain\s+to|risk[-\s]free\s+trade)\b",
    re.IGNORECASE,
)


class VoiceError(ValueError):
    pass


def lint(text: str) -> str:
    m = BANNED.search(text)
    if m:
        raise VoiceError(f"banned predictive language in alert copy: {m.group(0)!r}")
    return text


def complement_line(confidence: float) -> str:
    loss_pct = round((1 - confidence) * 100)
    return (f"Confidence {confidence:.0%} — this class loses ~{loss_pct}% of the time; "
            f"a loss here is normal, not a malfunction.")
