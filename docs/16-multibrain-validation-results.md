# Multi-brain technical regression validation

Run date: 2026-07-18  
Code: `technical-panel-regression-v1`  
Artifacts: `docs/evidence/multibrain-regression-21d.json` and
`docs/evidence/multibrain-regression-63d.json`

## Outcome

The regression candidates were **not promoted**. On both held-later validation
windows, the development-selected `core` regression trailed the expanding base
rate by 0.64% at 21 sessions and 4.12% at 63 sessions, and also had worse Brier
score than the existing transparent fixed-vote probability. The correct optimization is to
keep the simpler model authoritative, expose low confidence, and collect better
point-in-time evidence rather than tune more parameters against the same sample.

| Horizon | Validation samples | Base Brier | Fixed-vote Brier | Regression Brier | Relative change vs fixed vote | Symbols improved | Result |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 21 sessions | 360 | 0.22827 | 0.22759 | 0.22974 | -0.94% | 2 / 12 | No benchmark edge |
| 63 sessions | 108 | 0.21270 | 0.21928 | 0.22147 | -1.00% | 5 / 12 | No benchmark edge |

Lower Brier score is better. Directional accuracy was 65.0% for the 21-session
regression and 70.37% for the 63-session regression, but those figures mostly
reflect the positive base rate and did not translate into better probability
quality. Accuracy alone would therefore be a misleading promotion metric.

## Protocol

- Twelve currently configured symbols: AAPL, AMD, AMZN, GOOGL, META, MSFT,
  NVDA, QQQ, SMH, SPY, TQQQ, and TSLA.
- Ten years of corporate-action-adjusted daily history requested from the
  existing Yahoo provider.
- Non-overlapping 21- and 63-session outcomes.
- A record enters training only after its full forward outcome predates the test
  origin; same-date symbols share one frozen training set.
- Ridge logistic regression with training-only standardization.
- Two feature sets disclosed before validation: `core` and `risk_aware`.
- Candidate chosen on the first 70% of walk-forward predictions; the later 30%
  is the validation window.
- Comparisons: expanding positive base rate, empirical-Bayes fixed-vote state,
  and the selected regression.
- A positive label requires lower held-later Brier score than both the expanding
  base rate and fixed vote, plus broad symbol-level improvement.
- Current SEC filing facts, news, social attention, and earnings estimates were
  excluded because the repository has no point-in-time history for them.

## What changes in the product

- The research posture continues to use visible fixed technical votes.
- Forecasts that do not beat the expanding base rate remain `UNCONFIRMED`.
- Multi-brain agreement may organize evidence but does not manufacture
  statistical confidence.
- The UI must distinguish research posture from validated forward edge.
- The regression harness remains reproducible for future frozen datasets.

## Next legitimate accuracy improvements

1. Add a point-in-time constituent universe and delisted securities to reduce
   survivorship bias.
2. Archive filing availability dates and historical estimates/revisions before
   testing quality or earnings features.
3. Add benchmark-relative and sector-relative momentum with point-in-time sector
   membership, then pre-register one model change.
4. Hold the next candidate through an untouched forward paper period.
5. Expand beyond the technology-heavy current universe before making a
   cross-ticker generalization claim.

Do not add more thresholds or feature sets to these same validation windows and
then call the winner out-of-sample. That would turn optimization into selection
bias.
