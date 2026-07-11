# 17 — Trader Psychology Layer (Douglas + Tendler)

Sources: Mark Douglas, *Trading in the Zone*; Jared Tendler, *The Mental Game of Trading*. This layer is not decorative coaching copy — it defines (1) how the platform *speaks*, (2) what the trade log *detects*, and (3) an in-app **Mental Game toolkit**. It extends the Personal Discipline Layer (doc 00 §4) and the Discipline view (doc 10 §3).

## 1. The Probabilistic Voice (Douglas — platform-wide copy rules)

Grounded in the **Five Fundamental Truths** (anything can happen; you don't need to know what's next to make money; wins/losses are randomly distributed within an edge; an edge is just higher odds of one thing over another; every moment is unique):

| Copy rule | Implementation |
|---|---|
| Never predict | Banned words in alert/briefing copy: "will", "guaranteed", "sure", "can't lose". Alerts say "78% historical edge over n=214 setups", never "this will rise". Lint rule on all template strings. |
| Show the complement | Confidence always renders with its inverse: "78% — loses 22% of the time; a loss here is normal, not a malfunction" |
| No similarity framing | Alerts never say "just like last Tuesday's winner" (every moment is unique) |
| Streak neutralizer | After 2–3 consecutive wins *or* losses in a module, a banner: "Random distribution: recent outcomes carry zero information about this trade" — counters recency-weighted risk perception |
| Loss framing | Stop-out copy: "the edge didn't pay this time — cost of doing business", never "the market turned against you" |
| No open-position profit projections | Rigid rules, *flexible expectations* — projections manufacture rigid expectations |
| Process celebration only | No confetti on green P&L (random-reward conditioning); celebrations reserved for clean 20-trade samples, zero-error weeks, "paid yourself" streaks |

## 2. Consistency Framework (Douglas → features)

**Seven Principles of Consistency** mapped 1:1: (1) objectively identified edges = engine-only signals; (2) predefined risk = mandatory stops (already G3); (3) **risk acceptance** = one-tap "Risk accepted: max loss $X" acknowledgment on tracking a trade — placing a stop ≠ accepting it, so the dollar amount must be confronted; (4) act without hesitation = signal→execution latency measured; (5) pay yourself = tranche ladder with auto-breakeven (already doc 09) — after tranche 2 the trade card enters a distinct **RISK-FREE state** (shield badge), which Douglas calls the most important state to experience; (6) monitor error susceptibility = §3 detectors; (7) never violate = guardrails are blocks, not warnings.

Each closed trade gets a **Consistency Score** (0–7 principles honored); rolling average is the headline of the Discipline view — graded separately from P&L (RULE-C2).

**20-Trade Sample Mode** (the casino exercise): user commits to the next 20 signals of one module; platform pre-computes worst-case ("all 20 lose = $X — size down until that's acceptable"), locks parameters for the sample, logs every emitted signal as taken/skipped, and grades *execution fidelity only*. Skipped-signal counterfactual P&L is always shown (picking-and-choosing creates a random distribution of your own making). Completing a clean sample is the graduation criterion between **user tiers**: `MECHANICAL` (default: high override friction, full sizing automation) → `SUBJECTIVE` (manual adjustments unlocked, monitored) → `INTUITIVE` (advisory-only friction). Framed as advancement.

**Win-side euphoria guard** (Douglas's boom-and-bust insight — the mirror of our loss cooldowns): after N consecutive wins or a daily P&L spike ≥ 2× recent average, size caps *tighten* one notch for the next session and an "euphoria check" card appears. **Equity-threshold detector:** repeated give-backs near the same equity level are surfaced ("you've retreated from $X three times — invisible ceiling?").

## 3. Behavioral Error Detection (Douglas's error taxonomy × Tendler's signatures)

Auto-tagged from trade-log + interaction data; each tag names the error in the books' vocabulary and links its likely root (fear category / tilt type):

| Tag | Detection signature | Book source |
|---|---|---|
| `HESITATION` | Signal→ack/entry latency > threshold, or alert expired unacted then chased | Douglas (fear of being wrong) |
| `JUMPED_GUN` | Order staged before confirmation conditions completed | Douglas |
| `STOP_TAMPER` | Stop moved toward entry / widened after adverse move / deleted | Douglas ("most common of all errors") + Tendler tilt sign |
| `CHASE` / `FOMO` | Entry beyond the alert's no-chase bound after missing entry zone | Douglas (missing out) / Tendler FOMO ("belief there will never be another opportunity") |
| `REVENGE` | Re-entry same symbol/module within N min of stop-out, same direction, ≥1.5× size | Tendler ("anger plus a confidence flaw") |
| `OVERTRADE_BURST` | Session trade count > baseline × k after 2+ consecutive losses; idle-then-burst pattern | Tendler (tilt / boredom) |
| `EUPHORIA_SIZE` | Size escalation during win streak beyond budget curve | Douglas (boomers) / Tendler entitlement |
| `SNATCHED_PROFIT` | Exit far before TP1 with no thesis-break event | Douglas (leaving money) / Tendler fear of losing |
| `UNDERSIZE_FEAR` | Size < plan on valid signals during drawdown; strategy-hopping (module churn) | Tendler underconfidence |
| `AVERAGING_LOSER` | Adds to position beyond plan while under water | Tendler risk blindness |
| `PNL_FIXATION` | Open-P&L check frequency spike (app telemetry) | Tendler greed map level-1 |

The Discipline view becomes an **error-frequency dashboard grouped by root** (Douglas's four fears: being wrong, losing money, missing out, leaving money on the table; Tendler's tilt/confidence/discipline families) — "which fear drives you" is the summary stat. Existing module cooldowns (doc 00 §4) are retained and re-labeled as the zero-tolerance backstop; detections above trigger the *soft* intervention first (§5).

## 4. Mental Game Toolkit (Tendler — in-app features)

- **A/B/C game tagging:** one-tap self-tag per session (and optionally per trade) at close. Guided **A-to-C Game Analysis** builder: written descriptions of A/B/C levels in paired mental/tactical columns; locked from edits for 30 days (side-notes allowed). Dashboard shows the performance **range chart** (session-tag distribution) and flags front-end-only improvement (A-days improving while C-days unchanged) — the **Inchworm** rule: progress = the back end moving up; "your job is to suck less."
- **Emotional Map builder:** per problem (greed / fear / tilt / confidence / discipline), a 1–10 severity ladder with paired mental/technical cells (min 3 levels). Quick-capture button in-session (jot now, expand later); post-stop-out "detective mode" prompt; optional interval check-in timer logging a 1–10 level. Platform correlates logged levels with §3 detections to learn each user's ladder ("your level ≥5 sessions show 3× stop-widening") and can then alert on the behavioral proxy alone.
- **Mental Hand History (5-step guided flow):** 1) describe the problem → 2) why it makes sense you have it → 3) why that logic is flawed → 4) correction → 5) why the correction is correct. One screen per step; Step-2 coaching rejects "I'm just irrational"; Step-3 hint library drawn from Tendler's flaw taxonomies (illusion of control, perfectionism's confidence-debt, expecting to win every trade, misattributing luck, Beaten Dog Syndrome, etc.). Auto-prompted within 30 min of a high-emotion day's close. Its Step-4 correction becomes the user's **Injecting Logic** statement.
- **Pre-market Mental Warmup (3–5 min, gates module activation):** accumulated-emotion carryover check ("how are you arriving today?"), map review, Injecting Logic flashcard, Strategic Reminder (the user's own common-mistakes list). Skippable only with an explicit "skip logged" action — skipped-warmup streaks are themselves a discipline detection.
- **Post-market Cooldown (5–15 min within 30 min of close):** log/annotate trades, vent-writing box, map update; branch logic — stable day → reinforce what went right; hot day → route into a Mental Hand History.
- **Real-time intervention (the 4-step protocol, softened for an app):** when a §3 detection fires → (1) name the pattern from the user's own map, (2) momentum disruption: 30-second breathing screen or type-to-continue journal box before new orders in that module, (3) display the user's Injecting Logic statement, (4) show their Strategic Reminder. Quitting for the day is offered as a legitimate button, not a failure state.
- **Progress dashboard:** recognition rate (self-tag before vs. after the mistake), time-to-recovery after triggers, C-game error frequency trend. Framed as Tendler's **volume knob** (resolution is gradual), with his caveats rendered verbatim: *recognition ≠ control*, and the toolkit must never become an excuse detached from real-money results. Goal-language conversion: expectations → goals (perfectionism's "confidence debt" fix).

## 5. Intervention Ladder (unifying doc 00 §4, doc 13 G4/G5, and this layer)

1. **Nudge** — streak neutralizer banners, euphoria check, complement-visible confidence (always on).
2. **Friction** — detection fires → breathing/journal interstitial + Injecting Logic before next order in that module.
3. **Cooldown** — 2 consecutive stops (existing) or repeated detections → 90-min module cooldown ("Cooldown — protect the mind").
4. **Kill switch** — daily budget/account loss limits (G4), tightened size caps next session after euphoria or blowout days.

## 6. Discipline Reminder Library (rotating in briefing §8 and empty states)

From Douglas: "Anything can happen." · "You don't need to know what's going to happen next to make money." · "The consistency you seek is in your mind, not in the markets." · "Rigid in our rules and flexible in our expectations." · "Losses are simply the cost of doing business." · "Determine the risk and take the trade." · "I pay myself as the market makes money available to me." · "The market owes you nothing." · "When you genuinely accept the risks, you will be at peace with any outcome."
From Tendler: "Emotions are signals, not the problem." · "Your job is to suck less." · "Certainty is the antidote to fear." · "Patience is a byproduct of process." · "Tomorrow is a fantasy — start with five minutes today."
Placement rules: cost-of-business quotes on stop-out notices; pay-yourself on scale-out prompts; anything-can-happen on alert cards; risk-acceptance quote on the risk-acknowledgment tap.
