# TuneFolio project instructions

## Scope and status (2026-09-20)
Repository: https://github.com/RamMarthi9/tunefolio.
Local root: C:/Users/ramma/Documents/Ram/tunefolio.
Baseline HEAD: 2fcf8ab. The security, persistence, navigation and performance changes
described below were merged in PR #1 and deployed on 2026-09-20.
GitHub main release: 5ca30a042e7392559292dd2116bc1e06ee329c01.
Local Git metadata still points at baseline: fetch is denied for .git/FETCH_HEAD even
after a permission grant. Working files contain the release; do not overwrite them.
Read git status before changes. Preserve unrelated untracked `nul`.
The user approved this order: isolation/sessions/persistence/truthful states,
then overview/holdings/mobile navigation, then accurate history and explainable changes.
The current website was shown and tested in Chrome before development.

## Product constraints
Read-only portfolio intelligence. No order execution, predictions, investment advice
or automated investment decisions. Preserve vanilla frontend; no framework rewrite.
Never commit credentials, broker tokens, account ledgers, databases or live test data.
Use synthetic fixtures and isolated temporary databases for tests.
Missing, incomplete, cached and unavailable data must never silently become zero.
Do not label current-quantity backcasts as actual historical investment returns.

## Architecture
- FastAPI monolith: backend/app/main.py; auth/session/portfolio routes precede static mount.
- Vanilla frontend: frontend/index.html, assets/js/app.js, assets/js/navigation.js,
  assets/css/main.css. Chart.js and datalabels from CDN; no npm build.
- Main middleware protects every /portfolio/* and /holdings route, checks server-side
  expiry/revocation, derives account identity from the session, and establishes account_scope.
- services/db.py: central sessions.db plus one account database named by SHA-256 of
  verified broker user ID. get_connection() requires an account ContextVar. Only
  session functions use system_connection(). ThreadPool request context propagates;
  standalone/background threads must receive an explicit account and enter scope.
- Account DB tables: holdings_snapshots, instruments, trades, delivery_cache,
  index_cache, portfolio_observations, performance_events. Initializers are idempotent.
- Cookie is an opaque session UUID, HttpOnly/SameSite=Lax/Secure on HTTPS. No token
  fallback. Old compound cookies are rejected. Application expiry is at most 12h;
  broker rejection revokes the session sooner. Check auth BEFORE returning cached data.
- Broker holdings/margins cache is per session, 30s. HTTP timeout is 15s.
- Callback redirects relative to its own host, preventing cross-domain cookie loss.
- Background trade sync iterates newest valid session for each account; no global-token fallback.
- Legacy seed/runtime DB is never copied into accounts. Shared CSV import endpoint
  is disabled. Unowned historical data requires verified ownership before migration.
- SQLite production requires a persistent mounted TUNEFOLIO_DATA_DIR, one backend instance.
  Vercel requires TURSO_DATABASE_URL and TURSO_AUTH_TOKEN. remote_db.py uses direct
  libSQL connections (no local replica). Logical table names are mapped to server-selected
  SHA-256 account namespaces in one managed DB; this is application isolation, not DB RLS.
  System tables have a separate namespace. Never accept SQL or namespaces from clients.
  Partial configuration fails closed. Missing durable storage returns 503.
  Health probes the database; lazy system-schema initialization supports serverless ASGI.
  Production and Preview use separate Turso databases, injected by Vercel integration.
  No background scheduler/daemon on Vercel: History provides explicit today's-trades sync.
- Render blueprint defines a persistent disk and correct backend requirements path.
  Render is an alternative; the approved deployment target is GitHub plus Vercel/Turso.
- Sensitive API responses are no-store. Cross-origin writes are rejected.

## Financial conventions and limits
- INR en-IN. Retrieval timestamp is distinct from quote timestamp (currently unavailable).
- Broker previous-close movement excludes realised trades/cash flows; not dated daily return.
- Available cash uses margins.available.cash, not net including collateral.
- FIFO realised P&L pools symbol lots, applies April-March FY to sells; missing basis
  or no trades yields null totals. Gross of charges; corporate actions need reconciliation.
- performance.py links exact subperiod TWR using verified total valuations including
  cash immediately before every external flow. Rejects incomplete/unordered/nonfinite
  inputs and nonpositive post-flow capital. Liquidation/re-entry segmentation is unsupported.
- scripts/import_performance.py validates owner-bound ledger JSON, dry-run by default.
  --apply requires an account verified by broker sign-in and an empty existing series.
  Normalize stored timestamps to UTC. Verification is an operator attestation, not inferred.
- What changed compares two distinct broker observations. Price effect = opening qty
  Ã— price change; quantity effect = qty change Ã— ending price. Removed positions use
  their last observed quote. Do not infer sale proceeds or causes of quantity changes.
- Observations/snapshots are retrieval-driven, not guaranteed scheduled market-close data.
- Yahoo delivery fields are null, not zero. Public price caches are account-local for now.
- Legacy comparative view is explicitly an illustration using today's basket.

## UI conventions
Overview/Holdings/Performance/History navigation with mobile bottom bar. One allocation
chart at a time (current/invested). Holdings first, analysis beneath it. Search and native
sort controls work on mobile; table cells carry labels for stacked cards. Existing section
reordering is hidden and saved order no longer applied. Keep keyboard focus visible.
Always verify session regardless of status=connected query. Reconnect/retry banner instead
of forced authentication loops. Escape untrusted strings used in future HTML rendering.

## Commands
Python 3.10+; run from repository root:
    python -m venv .venv
    .venv\Scripts\python.exe -m pip install -r requirements-dev.txt
    .venv\Scripts\python.exe -m pytest -q
    .venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
    node --check frontend/assets/js/app.js
    node --check frontend/assets/js/navigation.js
Health /api/health; OpenAPI /docs. Set TUNEFOLIO_DISABLE_SCHEDULER=1 for local test previews.
Existing .venv points to a missing Python 3.10 installation on this machine. For this task,
tests use a separate .test-venv under the Codex task workspace; do not assume .venv works.
Configure backend/.env locally; never print its contents. Production storage variable alone
does not make an ephemeral disk durable. See docs/storage-and-performance.md for rollout,
ledger schema, ownership migration and outstanding constraints.

## Live baseline / outstanding work
- Chrome connected successfully. Production tunefolio.in loaded 24 holdings on 2026-09-19.
- Login redirected to tunefolio1.vercel.app and returned 401 while tunefolio.in worked;
  callback relative redirect fixes the code path, but live configuration is not changed.
- Render's previous service was suspended. Production now uses Vercel with Turso.
- No existing private history has been migrated; no production broker flow tested after edits.
- Restore/backup drill, real reconciled ledger import, and complete
  corporate-action/tax accounting remain outside the verified local implementation.

## Additional session hardening and verification
Login includes a one-use, 10-minute server-side state tied to an HttpOnly cookie.
Kite redirect_params returns that state; unmatched/replayed callbacks are rejected.
Start login on the configured callback origin. Secure cookies are mandatory in production.
Latest local validation: 51 tests pass (including libSQL adapter with local transport); JS syntax and diff whitespace checks pass.
Chrome mobile DOM/accessibility checks passed after fixing the old stacked-table labels;
final screenshot capture timed out. No production history migration performed.
Production deployment subsequently completed; see Managed storage rollout below.

## Managed storage rollout
Turso Starter ($0/month) installed in Vercel, US East (Virginia). Production resource
`tunefolio-db` connected only to Production. `tunefolio-preview-db` is for Preview only.
Vercel production deployment GxCLhopcGTvc98HoD1HL2uU114Gc is Ready.
https://tunefolio.in/api/health returned 200 with storage=reachable; unauthenticated
/portfolio/performance and /session/active returned 401 and Cache-Control: no-store.
Chrome verified new production navigation and reconnect state. Preview login-state
write reached Zerodha login; completing broker authentication remains a user action.
Never put production DB credentials in Preview or Git. Broker sign-in is still required
after cutover; no legacy portfolio or historical ledger is migrated automatically.

## Quantity and reconciliation correction (2026-09-20)
- Broker fetch returns copies normalized by valuation.normalize_holding: settled + T1
  + MTF quantities. MTF cost uses its own average price. Do not add used/collateral
  quantities. Retain raw observations and normalize on read to avoid false settlement
  changes. All portfolio consumers use the same quantity basis.
- FY dates use India time, April 1 rollover; UI displays FYyyyy-yy, never YTD.
- Cash breakdown exposes raw cash, current balance, opening balance, collateral,
  net margin and pay-in. Do not choose a fallback merely because raw cash is zero.
- trade_reconciliations is account-scoped. A realised total requires a reviewed
  period receipt tied to a deterministic ledger digest, plus complete FIFO basis.
  Imports/syncs alone do not verify coverage. Do not fabricate reconciliation receipts.
- Existing private history is incomplete for the current FY. User confirmed same
  broker account and additional recent trades. Obtain current Console exports before
  migration; no historical production import has been performed.

- PR #2 merged and deployed at b941e5f76379e18a84369cdf0cf75bb3f4a45a32.
  Live holdings totals reconciled with Kite. Cash API raw cash was zero while
  available.live_balance matched Kite. Use live_balance as Available balance (equity),
  retain raw breakdown, never describe it as a withdrawable cash amount.
- Runtime revealed Turso SQLITE_BUSY interactive transaction expiry during per-row
  instrument inserts. Batch inserts in bounded statements and close on errors.
