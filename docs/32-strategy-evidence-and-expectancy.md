# 32 — Strategy Evidence Object & Net-Expectancy Gate

**Status:** Implemented on branch `feat/strategy-evidence-expectancy-macd-shadow`
**Implements:** docs/18-enhancement-review.md §5.3–5.5 and §12.5–12.6 (slice)

## 1. StrategyEvidence schema

`Setup` and `Alert` now carry an optional `evidence: StrategyEvidence` object:

| Field | Meaning |
|---|---|
| `evidence_status` | `UNRESEARCHED` … `LIVE_VALIDATED` / `DISABLED` |
| `point_estimate` | Historical base rate or bucket WR — **not** a composite-score probability |
| `interval_95` | Wilson or documented interval when available |
| `sample_size` | Effective n |
| `avg_r_pre_cost` / `expected_net_r` | Economic edge after declared cost stress |
| `limitations` / `note` | Human-readable caveats from calibration meta |

Built by `CalibrationMap.build_evidence(composite)` from `config/calibration/{STRATEGY}.json`.

## 2. Net-expectancy gate

For `pop_based` strategies on the **live** path (not global SHADOW, not research-only):

```text
expected_net_r = avg_r_pre_cost − cost_stress_r
pass if expected_net_r ≥ 0  OR  evidence_status == LIVE_VALIDATED
        OR  expectancy not declared in meta
```

Failures emit `SuppressedSetup` with `gate="expectancy"`.

Research / `shadow_monitor` paths still record setups so forward evidence can accumulate.

## 3. PULLBACK_CONTINUATION_MACD

Separate registry identity:

- Same geometry as `PULLBACK_CONTINUATION`
- Additional filter: completed H1 `macd_histogram > 0`
- `live_eligible=False`, `shadow_monitor=True`
- Own calibration file with `IN_SAMPLE_ONLY` status — **never** reuses the 76.5% map

Promotion still requires the full doc 08 / doc 18 gates (parity, forward paper, costs).

## 4. Trend strategy suitability

`intellidhan_analytics.strategy_context.trend_strategy_suitability` maps daily consensus + breakout state to Swing / 0DTE **context notes** only. It does not create alerts or raise confidence.

## 5. Non-goals (still open)

- Full research ↔ production parity for pullback populations
- Option-level fill realism
- Score-conditional isotonic calibration with live samples
- Automatic promotion of any strategy
