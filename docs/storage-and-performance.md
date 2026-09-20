# Storage, rollout and performance

Changes are being deployed through PR #1. Check Vercel deployment status before
assuming production is current. Never deploy the serverless backend without managed storage.

## Durable deployment

For the local SQLite mode, the supported backend is a persistent Python process with a mounted disk.
`TUNEFOLIO_DATA_DIR` must point to that disk in production. `render.yaml` now
declares `/var/data/tunefolio`; provisioning the service/disk is a separate action.
Use a single backend instance with this local SQLite design. Back up the mounted
directory with a SQLite-aware backup process, encrypt backups, and test restoration.
Setting the variable alone does not turn an ephemeral filesystem into durable storage.

Vercel local storage is explicitly refused (503 for auth and portfolio routes).
Vercel uses the direct libSQL adapter with TURSO_DATABASE_URL and TURSO_AUTH_TOKEN.
Vercel's Turso integration injects these secrets; never commit or print them. Production
and Preview must connect to different databases. The current free Starter installation
uses US East (Virginia). Health checks perform an actual database query.

Remote account tables use a SHA-256 namespace selected only from the verified session;
the session registry has its own system namespace. This is application-enforced isolation
within one managed database, not provider row-level security. SQL is owned by the application,
parameters hold values, and callers cannot supply physical table names or SQL.
There is no serverless /tmp replica or fallback. Namespace schema creation is idempotent.
The remote adapter's DB-API/transaction/isolation tests run using local libSQL; a deployed
health/login check is separately required to verify remote transport.

Vercel does not run the persistent scheduler or callback daemon. Users can explicitly
sync today's broker trades from History. Past trades still need an owner-verified import.
Configure provider backups and perform a restore drill before relying on recovered history;
that drill has not yet been performed.

Serve the frontend and API through the same public origin. Configure the broker
callback on that origin at `/auth/zerodha/callback`. The callback now redirects
to a relative URL, so it no longer moves a host-only cookie to another domain.
All existing compound cookies must be replaced by signing in again.

## Ownership and migration

The session registry is `sessions.db`. Every verified broker user gets a separate
database named with SHA-256 of their user ID under `accounts/`. Request context
comes only from a valid server-side session; query parameters cannot select owners.
Background jobs must receive an explicit account ID. An unscoped portfolio DB
connection fails. Both session and portfolio files need backup/restoration.

The old runtime database and bundled seed are **not** copied automatically.
They have no ownership metadata. Preserve an offline backup and establish their
owner before any migration. Do not make the first signed-in user their owner.
The old shared-directory tradebook import endpoint is disabled. Existing scripts
that relied on an unscoped connection must be run with an explicit account scope;
they intentionally fail rather than write into an arbitrary account.

## Accurate history

Historical performance uses exact linked time-weighted subperiod returns.
Each event contains an ISO timestamp with timezone, total portfolio value including
cash immediately **before** an external flow, the signed flow, source, and verified
coverage. The first event establishes the baseline. Deposits are positive and
withdrawals negative. Include a valuation at the reporting endpoint, with zero flow.
Every external flow must have its pre-flow valuation; fees, dividends and corporate
actions must already be reconciled into the portfolio valuations. Imported coverage
is a human attestation, not something the calculator can establish from trades.
Accounts with zero post-flow capital or incomplete history return an unavailable
state rather than an invented percentage. Full liquidation/re-entry needs separate
performance segments and is not yet supported.

`python -m scripts.import_performance ledger.json --account USER_ID` validates an
account-bound file without writing. `--apply` imports into a previously broker-verified
account only when no series exists. Keep ledgers outside Git. Example shape (synthetic):

```json
{
  "account_id": "EXAMPLE",
  "complete_external_flow_coverage": true,
  "events": [
    {"observed_at":"2026-04-01T10:00:00+00:00","value_before_flow":100,"external_flow":0,"source":"reconciled ledger","coverage_verified":true},
    {"observed_at":"2026-04-02T10:00:00+00:00","value_before_flow":110,"external_flow":100,"source":"reconciled ledger","coverage_verified":true},
    {"observed_at":"2026-04-03T10:00:00+00:00","value_before_flow":231,"external_flow":0,"source":"reconciled ledger","coverage_verified":true}
  ]
}
```

The result is 21%, not the raw balance increase of 131%. This implementation does
not infer historical cash balances from today's holdings. The old comparative view
is explicitly labelled a current-basket illustration, not historical performance.
FIFO realised P&L is gross of charges; unmatched sells and absent trade history
produce unavailable totals. It is not an audited tax statement.

## Explainable changes

Two distinct broker retrievals establish the comparison window. Holding-value
change equals opening quantity × price movement plus quantity change × ending
price. A removed position uses its last observed price and does not infer sale
proceeds. Causes such as trades, transfers and corporate actions are not guessed.
Observations are retrieval-time evidence, not independently scheduled market closes.
The broker quote timestamp is currently unavailable and the UI says so.

## Validation of the local implementation
- 51 synthetic-data tests pass (account isolation, concurrent scopes, persistence across connections, session expiry/revocation, authenticated routes, broker rejection, login-state replay, missing FIFO basis, missing delivery data, change decomposition and cash-flow-adjusted returns).
- Both JavaScript files pass Node syntax checks; git diff --check passes.
- Chrome DOM checks: authenticated overview, holdings navigation/search, mobile labels and overflow, allocation basis toggle, incomplete-history message, change explanations, logout/reconnect. No error/warning entries were captured in the synthetic preview for those flows.
- Mobile screenshot revealed the old table CSS problem; it was corrected and verified through accessibility/DOM checks. Subsequent screenshot capture repeatedly timed out, so a final visual screenshot pass is still pending.
- All test holdings/tokens/ledgers were synthetic. Production login, existing data migration and backup restoration remain unverified; managed storage is provisioned separately through Vercel.
- Login anti-forgery state uses the broker's documented redirect_params round trip: https://www.kite.trade/docs/connect/v3/user/#login-flow . States expire after 10 minutes, are consumed once and must match the HttpOnly browser cookie. Begin login on the configured callback origin.
