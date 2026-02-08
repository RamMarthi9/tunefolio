TuneFolio

Finance with Finesse

TuneFolio is a read-only portfolio intelligence platform that helps investors understand why their portfolio behaves the way it does — using secure, consent-based integration with Zerodha.

It focuses on explanation over execution, turning raw portfolio data into structured, time-aware insights.

Why TuneFolio

Most portfolio tools answer “What is my P&L right now?”
TuneFolio is built to answer deeper questions:

Why did my portfolio perform this way?

Which decisions contributed to gains or losses?

How has my portfolio evolved over time — not just today?

Core Ideas

Time-aware portfolio analysis (SoD / EoD snapshots)

Decision attribution, not predictions

Historical context over point-in-time noise

What TuneFolio Does Today
✅ Secure Zerodha Integration

OAuth-based login

Read-only access (no trades, no execution)

Active session management

✅ Portfolio Overview

Live holdings with:

Quantity

Average buy price

Current price

Invested value

Current value

Real-time P&L

Auto-calculated portfolio KPIs:

Total invested

Current portfolio value

Net P&L

✅ Instrument & Sector Intelligence

Instruments auto-created from holdings

Sector & industry enrichment (auto-fetched, cached)

No manual tagging when new stocks are added

✅ Historical Data Foundation

Snapshot-based holdings storage

Designed for:

Start-of-Day (SoD) snapshots

End-of-Day (EoD) snapshots

Enables future:

Time-series analysis

Performance attribution

Trading journals

✅ Frontend Dashboard

Lightweight HTML / CSS / JavaScript UI

KPI cards + holdings table

Designed to organically embrace future features
(sector allocation, journals, analytics)

What TuneFolio Does Not Do

❌ No trading or execution

❌ No investment advice

❌ No predictions or signals

❌ No automated decision-making

TuneFolio is an explainability layer, not a trading terminal.

Product Principles

Insight over action

Context over noise

Trust over automation

Data as a product

Architecture (Current)
Frontend

Static HTML / CSS / JavaScript

API-driven

GitHub Pages compatible

Backend

FastAPI (Python)

Zerodha OAuth integration

SQLite (current) with a clear path to MySQL / PostgreSQL

Modular services:

Authentication

Holdings

Instruments

Portfolio intelligence

Status

🟢 Core platform operational

Authentication: ✅

Live holdings: ✅

Portfolio KPIs: ✅

Sector enrichment: ✅

Snapshot storage: ✅

🚧 In progress

SoD / EoD snapshot guards

Time-series portfolio views

Sector allocation analytics

Trading journal & decision history

Vision

TuneFolio aims to become a personal investment memory system —
where data doesn’t just report outcomes, but explains decisions over time.
