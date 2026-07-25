"""Calibration maps — composite score → claimed confidence (doc 03 §4).

A strategy's map is fitted from SHADOW-mode paper outcomes: bucketed realized
win rates with Laplace smoothing, made monotone non-decreasing in composite
(pool-adjacent-violators). Buckets under MIN_N are 'insufficient data' and fall
back to the conservative v0 map — claims are never built on thin samples
(doc 10 §4). Tables live in config/calibration/{STRATEGY}.json with fit
metadata for auditability (G8).

Also exposes StrategyEvidence and expected-net-R helpers (doc 18 §5.3–5.5).
"""

from __future__ import annotations

import json
from pathlib import Path

from intellidhan_schemas.signals import EvidenceStatus, StrategyEvidence

MIN_N = 15
BUCKET = 5.0
CONSERVATIVE_FACTOR = 0.90  # v0 fallback: composite/100 × 0.90
MAX_UNVALIDATED_CONFIDENCE = 0.74  # below the standard 0.75 live gate
VALIDATED_EVIDENCE_STATUSES = {"HISTORICAL_OOS", "FORWARD_PAPER", "LIVE_VALIDATED"}
DEFAULT_COST_STRESS_R = 0.05
# POP-based strategies must clear this lower bound after cost stress when meta
# provides expectancy numbers (doc 18 §5.5). LIVE_VALIDATED may still pass.
MIN_NET_EXPECTANCY_R = 0.0

CALIB_DIR = Path("config/calibration")


def conservative(composite: float) -> float:
    return round(min(composite / 100.0 * CONSERVATIVE_FACTOR, CONSERVATIVE_FACTOR), 4)


def expected_net_r(
    win_rate: float,
    avg_win_r: float,
    avg_loss_r: float = 1.0,
    cost_r: float = DEFAULT_COST_STRESS_R,
) -> float:
    """Conservative expected net R after a flat per-trade cost stress.

    avg_loss_r is the magnitude of a loss in R units (positive number).
    """
    if not 0.0 <= win_rate <= 1.0:
        raise ValueError("win_rate must be in [0, 1]")
    gross = win_rate * avg_win_r - (1.0 - win_rate) * avg_loss_r
    return round(gross - cost_r, 6)


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

    def build_evidence(self, composite: float | None = None) -> StrategyEvidence:
        """Materialize the honest evidence object from calibration meta + buckets."""
        status_raw = self.evidence_status
        try:
            status = EvidenceStatus(status_raw)
        except ValueError:
            status = EvidenceStatus.UNVALIDATED

        if self.meta.get("live_eligible") is False and status not in {
            EvidenceStatus.DISABLED, EvidenceStatus.DEMOTED,
        }:
            # Administrative block is stronger than historical OOS for live claims.
            if status == EvidenceStatus.HISTORICAL_OOS:
                status = EvidenceStatus.HISTORICAL_OOS  # keep label; runner still blocks live

        sample_size = sum(int(b.get("n", 0)) for b in self.buckets.values()) or None
        point = None
        if self.buckets:
            # Prefer the bucket that would fire for this composite; else first sufficient.
            if composite is not None:
                for key, bucket in self.buckets.items():
                    lo, hi = (float(x) for x in key.split("-"))
                    in_bucket = lo <= composite < hi or (composite == 100.0 and hi == 100.0)
                    if in_bucket:
                        point = float(bucket["wr"])
                        sample_size = int(bucket.get("n", sample_size or 0)) or sample_size
                        break
            if point is None:
                for bucket in self.buckets.values():
                    if bucket.get("sufficient"):
                        point = float(bucket["wr"])
                        break

        interval = None
        raw_interval = self.meta.get("interval_95_test") or self.meta.get("interval_95")
        if isinstance(raw_interval, (list, tuple)) and len(raw_interval) == 2:
            interval = (float(raw_interval[0]), float(raw_interval[1]))

        avg_r_pre = self.meta.get("avg_r_pre_cost")
        if avg_r_pre is None:
            # Parse common "expectancy": "avg_r +0.041 val / +0.044 test..." style notes.
            avg_r_pre = self.meta.get("avg_r")
        avg_r_pre_f = float(avg_r_pre) if avg_r_pre is not None else None

        pf = self.meta.get("profit_factor")
        pf_f = float(pf) if pf is not None else None

        cost = float(self.meta.get("cost_stress_r", DEFAULT_COST_STRESS_R))
        net = None
        if avg_r_pre_f is not None:
            net = round(avg_r_pre_f - cost, 6)
        elif point is not None and self.meta.get("assume_1r_loss", True):
            # High-POP modest-R class: approximate avg win from PF when available,
            # else assume symmetric 1R loss and unknown win size → skip net claim.
            if pf_f is not None and point > 0:
                # PF = (wr * W) / ((1-wr) * L); L=1 → W = PF * (1-wr) / wr
                avg_win = pf_f * (1.0 - point) / point if point < 1 else pf_f
                net = expected_net_r(point, avg_win, 1.0, cost)

        limitations = list(self.meta.get("limitations") or [])
        caveats = self.meta.get("caveats")
        if isinstance(caveats, str) and caveats and caveats not in limitations:
            limitations.append(caveats)

        return StrategyEvidence(
            evidence_status=status,
            probability_kind=str(self.meta.get("probability_kind", "STRATEGY_BASE_RATE")),
            point_estimate=point,
            interval_95=interval,
            sample_size=sample_size,
            profit_factor=pf_f,
            avg_r_pre_cost=avg_r_pre_f,
            avg_r_net_cost=net,
            cost_stress_r=cost,
            expected_net_r=net,
            calibration_version=str(self.meta.get("analytics_version") or "uncalibrated-v0"),
            limitations=limitations,
            note=self.meta.get("evidence_note"),
        )

    def passes_expectancy_gate(self, *, research_only: bool = False) -> tuple[bool, str]:
        """doc 18 §5.5 — POP/high-hit strategies need non-negative net R after cost.

        Returns (passed, detail). Missing expectancy data does not fail the gate
        (unknown ≠ negative); LIVE_VALIDATED always passes; research/shadow may
        still be recorded even when the gate would fail live delivery.
        """
        if self.evidence_status == "LIVE_VALIDATED":
            return True, "live-validated evidence"
        evidence = self.build_evidence()
        if evidence.expected_net_r is None:
            return True, "expectancy not declared in calibration meta"
        if evidence.expected_net_r >= MIN_NET_EXPECTANCY_R:
            return True, f"expected_net_r={evidence.expected_net_r}"
        if research_only:
            return True, (
                f"expected_net_r={evidence.expected_net_r} below gate but "
                f"research/shadow path allowed"
            )
        return False, (
            f"expected_net_r={evidence.expected_net_r} < {MIN_NET_EXPECTANCY_R} "
            f"after {evidence.cost_stress_r}R cost stress"
        )

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
