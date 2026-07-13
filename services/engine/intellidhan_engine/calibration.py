"""Calibration maps — composite score → claimed confidence (doc 03 §4).

A strategy's map is fitted from SHADOW-mode paper outcomes: bucketed realized
win rates with Laplace smoothing, made monotone non-decreasing in composite
(pool-adjacent-violators). Buckets under MIN_N are 'insufficient data' and fall
back to the conservative v0 map — claims are never built on thin samples
(doc 10 §4). Tables live in config/calibration/{STRATEGY}.json with fit
metadata for auditability (G8).
"""

from __future__ import annotations

import json
from pathlib import Path

MIN_N = 15
BUCKET = 5.0
CONSERVATIVE_FACTOR = 0.90  # v0 fallback: composite/100 × 0.90
MAX_UNVALIDATED_CONFIDENCE = 0.74  # below the standard 0.75 live gate
VALIDATED_EVIDENCE_STATUSES = {"HISTORICAL_OOS", "FORWARD_PAPER", "LIVE_VALIDATED"}

CALIB_DIR = Path("config/calibration")


def conservative(composite: float) -> float:
    return round(min(composite / 100.0 * CONSERVATIVE_FACTOR, CONSERVATIVE_FACTOR), 4)


class CalibrationMap:
    """Per-strategy composite→confidence map."""

    def __init__(self, strategy: str, buckets: dict[str, dict] | None = None,
                 meta: dict | None = None) -> None:
        self.strategy = strategy
        self.buckets = buckets or {}
        self.meta = meta or {}

    # ----- fitting -----

    @classmethod
    def fit(cls, strategy: str, samples: list[tuple[float, bool]], meta: dict | None = None
            ) -> "CalibrationMap":
        """samples: (composite, won). Laplace-smoothed bucket WRs, then PAV monotone."""
        raw: dict[float, dict] = {}
        for comp, won in samples:
            lo = comp // BUCKET * BUCKET
            b = raw.setdefault(lo, {"n": 0, "wins": 0})
            b["n"] += 1
            b["wins"] += 1 if won else 0
        ordered = sorted(raw.items())
        # pool-adjacent-violators for monotone WR in composite
        pools: list[dict] = []
        for lo, b in ordered:
            pool = {"lo": lo, "hi": lo + BUCKET, "n": b["n"], "wins": b["wins"]}
            pools.append(pool)
            while len(pools) >= 2 and _wr(pools[-1]) < _wr(pools[-2]):
                a = pools.pop()
                pools[-1]["hi"] = a["hi"]
                pools[-1]["n"] += a["n"]
                pools[-1]["wins"] += a["wins"]
        buckets = {
            f"{p['lo']:.0f}-{p['hi']:.0f}": {
                "n": p["n"], "wr": round(_wr(p), 4), "sufficient": p["n"] >= MIN_N,
            }
            for p in pools
        }
        return cls(strategy, buckets, meta or {})

    # ----- lookup -----

    @property
    def evidence_status(self) -> str:
        return str(self.meta.get("evidence_status", "UNVALIDATED")).upper()

    @property
    def has_validated_evidence(self) -> bool:
        return self.evidence_status in VALIDATED_EVIDENCE_STATUSES

    def confidence(self, composite: float) -> float:
        if self.has_validated_evidence:
            for key, bucket in self.buckets.items():
                lo, hi = (float(x) for x in key.split("-"))
                in_bucket = lo <= composite < hi or (composite == 100.0 and hi == 100.0)
                if in_bucket and bucket["sufficient"]:
                    # Truth 1: anything can happen; never claim more than 95%.
                    return min(bucket["wr"], 0.95)
        # Composite score is not a forward probability.  Until the strategy
        # has explicit OOS/forward evidence, fail below the normal live gate.
        return min(conservative(composite), MAX_UNVALIDATED_CONFIDENCE)

    # ----- persistence -----

    def save(self, directory: Path = CALIB_DIR) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.strategy}.json"
        path.write_text(json.dumps(
            {"strategy": self.strategy, "buckets": self.buckets, "meta": self.meta}, indent=2))
        return path

    @classmethod
    def load(cls, strategy: str, directory: Path = CALIB_DIR) -> "CalibrationMap":
        path = directory / f"{strategy}.json"
        if not path.exists():
            return cls(strategy)  # empty map → conservative fallback everywhere
        data = json.loads(path.read_text())
        return cls(strategy, data["buckets"], data.get("meta", {}))


def _wr(pool: dict) -> float:
    return (pool["wins"] + 1) / (pool["n"] + 2)  # Laplace smoothing
