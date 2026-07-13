# Accounts and Personal Settings

**Status:** implemented foundation · **Updated:** 2026-07-13

This document is the agent-readable contract for identity and user-owned state.
It supersedes references to the web terminal as an "owner-only" application.
The legacy owner token remains only as a first-administrator and emergency
compatibility path.

## 1. User-facing vocabulary

Use plain language in the product UI. Technical names may remain in code,
telemetry, and research documentation.

| Internal or research term | UI wording |
|---|---|
| owner session | account / signed in |
| budgets | capital and risk limits |
| auction state | market direction |
| POC | key price level |
| value area / VA | fair-value range |
| one-timeframing / 1TF | directional trend |
| profile shape | market pattern |
| calibration / evidence | historical reliability |
| 95% CI | likely range |
| sample n | past signals |
| R:R | reward versus risk |
| suppression gate | reason a signal was held back |

Do not rewrite strategy research terminology in machine contracts. Translate it
at the presentation boundary so audits and historical payloads remain stable.

## 2. Identity model

Accounts use normalized lowercase email addresses and one of three roles:

- `ADMIN`: personal access, shared automation control, and account creation.
- `TRADER`: personal research state. It cannot change shared automation until
  execution policy and capital enforcement are user-scoped.
- `VIEWER`: personal research state only; automation control is denied.

Passwords are 12–128 characters and stored as salted scrypt hashes. Scrypt
parameters are fixed and validated before hashing so database values cannot
request unbounded work. A login creates a 256-bit opaque token in an HttpOnly,
SameSite=Strict cookie. Only the
SHA-256 digest of that token is stored in `user_sessions`; sessions expire after
12 hours and are revoked in the database at logout. WebSocket authorization
uses the same database session, closes at its server-side expiry, and
periodically revalidates the session so a live socket cannot outlast revocation.

The `INTELLIDHAN_OWNER_TOKEN` path still creates the older signed owner cookie.
It maps to a temporary ADMIN principal named `legacy-owner`, cannot save personal
preferences, and should be used to create the first durable account. The raw
owner token is never placed in a browser cookie or database.

## 3. Account creation

There are two supported flows:

1. `POST /api/auth/register` creates the first account as `ADMIN`. Its
   `invite_code` must match `INTELLIDHAN_OWNER_TOKEN`; the general invite code
   can never claim the initial administrator role.
2. A signed-in administrator uses `POST /api/accounts` to add an `ADMIN`,
   `TRADER`, or `VIEWER`. If `INTELLIDHAN_INVITE_CODE` is configured, invited
   users may also self-register as `TRADER`.

Registration and login are IP-rate-limited in process. Responses never include
password hashes or session digests. Duplicate emails fail with a validation
error. Account creation, default preferences, capital limits, and the initial
watchlist are committed in one transaction. The initial administrator claim is
serialized with a database lock, preventing two concurrent bootstrap requests
from both receiving that role.

## 4. Database ownership

Shared research/engine state remains in the existing operational tables.
Personal records use dedicated tables so this migration does not destructively
rewrite legacy installations:

```text
users
  ├── user_sessions
  ├── user_preferences
  ├── user_capital_limits
  ├── user_watchlists ── user_watchlist_members
  └── user_saved_screens
```

Every personal query includes `user_id`; watchlist and saved-screen identifiers
are unique within a user rather than globally. A new account is seeded with the
current default preferences, the configured capital/risk defaults, and a
`Research` watchlist.

PostgreSQL through `DATABASE_URL` is required for durable Koyeb accounts. The
SQLite fallback is suitable for local development only unless it is on a
mounted persistent volume. Losing an ephemeral SQLite file loses accounts,
password hashes, sessions, preferences, limits, watchlists, and screens.

## 5. API contract

| Method and path | Purpose | Access |
|---|---|---|
| `GET /api/auth/session` | configuration, signed-in user, role, registration availability | public |
| `POST /api/auth/register` | invited/first-admin account creation and sign-in | invite/setup code |
| `POST /api/auth/session` | email/password login; legacy `{token}` remains compatible | public, rate-limited |
| `DELETE /api/auth/session` | revoke database session and clear both cookies | signed in |
| `GET /api/accounts` | list safe account metadata | ADMIN |
| `POST /api/accounts` | add an account and seed personal state | ADMIN |
| `GET/PUT /api/account/preferences` | load or save display preferences | durable account |
| `GET/PUT /api/budgets` | load or save the current user's capital/risk limits | signed in |
| `/api/watchlists`, `/api/screens` | user-isolated research state | signed in |
| `/api/state`, `/api/briefing`, `/api/autotrade`, `/ws` | protected terminal state | signed in |

Supported preferences are `theme`, `default_view`, `compact_cards`,
`alert_sound`, and `reduced_motion`. The current UI applies theme, start page,
card density, and reduced motion. Alert sound is persisted for a later
notification-delivery slice and appears disabled as **coming soon** in the UI.

## 6. Capital-limit boundary

The auto-trade policy is deployment-wide, not personal. An `ADMIN` change
affects every user and the single connected Robinhood execution agent. The
policy dialog must disclose this boundary next to the real-order warning.

Capital and risk limits are now durable and user-specific. They are displayed
as a personal review ceiling against shared signal-plan risk.

They do **not yet** resize shared engine alerts, change global strategy
composition, or constrain a shared auto-trade intent. The UI states this
limitation directly. Before multi-user live execution, add a user-specific order
planning layer that derives quantity from the selected user's capital, holdings,
open risk, and broker buying power, then enforces the same limits again at order
placement.

Never update `config/budgets.yaml` from a durable account request. Only the
legacy administrator route retains the old global hot-reload behavior for
backward compatibility.

## 7. Security work still required

This is an invite-only foundation, not a finished public identity platform.
Before public exposure:

- add CSRF tokens to cookie-authenticated mutations;
- add password change/reset, account disable/reactivate, and forced session
  revocation workflows;
- replace process-local rate limits with identity/IP limits in shared storage;
- add session/device management and security audit events;
- add email ownership verification and invitation expiry/one-time use;
- enforce per-user capital and portfolio limits in order planning and execution;
- add database migrations managed by a versioned migration tool.

## 8. Required verification

The account test slice must prove salted password hashing, opaque session-token
storage, expiry and logout revocation, first-admin setup, invite failure,
administrator-created roles, role denial, persistence across database reopen,
and cross-user isolation for preferences, capital limits, watchlists, and saved
screens. CI also runs `tests/test_accounts_postgres.py` against PostgreSQL 16 to
verify schema setup, rollback, concurrent bootstrap locking, sessions, and
personal-state persistence on the production backend. Keep the legacy
owner-cookie tests passing until that route is removed in a separately reviewed
migration.
