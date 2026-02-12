# TuneFolio — MVP Architecture Document

> **Version:** 1.0
> **Scope:** Login to Zerodha, fetch holdings, sector enrichment, sector-wise portfolio visualization
> **Principles:** Cost-effective ($0-5/mo), scalable from day-1 patterns, production-ready MVP

---

## Table of Contents

1. [Recommended Tech Stack](#1-recommended-tech-stack)
2. [High-Level Architecture (HLA)](#2-high-level-architecture-hla)
3. [Low-Level Architecture (LLA)](#3-low-level-architecture-lla)
4. [Database Schema & Data Models](#4-database-schema--data-models)
5. [API Contracts](#5-api-contracts)
6. [Frontend Component Architecture](#6-frontend-component-architecture)
7. [Authentication Flow](#7-authentication-flow)
8. [Data Pipeline: Holdings → Sectors → Visualization](#8-data-pipeline)
9. [Deployment Architecture](#9-deployment-architecture)
10. [Testing Strategy](#10-testing-strategy)
11. [Migration Path from Current Codebase](#11-migration-path)

---

## 1. Recommended Tech Stack

### Why These Tools

Every tool below was chosen against three criteria:
1. **Cost** — Free tier or <$5/mo for MVP
2. **Speed-to-build** — Smallest learning curve, best FastAPI integration
3. **Scale path** — Won't need a rewrite when going from 1 user to 1000

### Stack Table

```
┌─────────────────────┬──────────────────────┬────────────────────────────────────┐
│ Layer               │ Tool                 │ Why                                │
├─────────────────────┼──────────────────────┼────────────────────────────────────┤
│ Backend Framework   │ FastAPI + Uvicorn    │ Async-native, auto-docs, Pydantic  │
│ ORM / Data Layer    │ SQLModel             │ Same author as FastAPI, Pydantic   │
│                     │                      │ + SQLAlchemy in one model          │
│ Database            │ SQLite (→ Postgres)  │ Zero-infra MVP, 1-line migration   │
│ Auth (Broker)       │ Zerodha OAuth 2.0    │ Required for holdings access       │
│ Auth (App Users)    │ PyJWT + pwdlib       │ Official FastAPI recommendation    │
│ Task Scheduling     │ APScheduler          │ In-process, cron triggers, async   │
│ Frontend            │ Jinja2 + HTMX        │ Zero build step, server-rendered   │
│ Charts              │ Chart.js (~50KB)     │ Pie, doughnut, bar — lightweight   │
│ Financial Charts    │ Plotly.js            │ Candlestick, time-series, zoom     │
│ Testing             │ pytest + httpx       │ FastAPI standard, async support    │
│ Code Quality        │ Ruff + Mypy          │ Replaces Black+Flake8+isort        │
│ Package Management  │ uv + pyproject.toml  │ 10-100x faster than pip            │
│ Deployment          │ Docker + Render      │ Free tier → $7/mo always-on        │
│ CI/CD               │ GitHub Actions       │ Free for public repos              │
└─────────────────────┴──────────────────────┴────────────────────────────────────┘
```

### Monthly Cost Breakdown

```
┌────────────────────────┬──────────┬──────────────┐
│ Component              │ MVP      │ Growth       │
├────────────────────────┼──────────┼──────────────┤
│ Backend (Render/Rly)   │ $0       │ $5-7/mo      │
│ Database (SQLite)      │ $0       │ $0 (Neon PG) │
│ Frontend (Static)      │ $0       │ $0           │
│ Domain                 │ $0       │ $10/yr       │
│ CI/CD (GitHub Actions) │ $0       │ $0           │
├────────────────────────┼──────────┼──────────────┤
│ TOTAL                  │ $0/mo    │ ~$5-7/mo     │
└────────────────────────┴──────────┴──────────────┘
```

---

## 2. High-Level Architecture (HLA)

```
                                    TUNEFOLIO — HIGH-LEVEL ARCHITECTURE
                                    ════════════════════════════════════

    ┌─────────────────────────────────────────────────────────────────────────────────┐
    │                              USER'S BROWSER                                     │
    │                                                                                 │
    │  ┌──────────────┐   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐     │
    │  │  Dashboard    │   │  Sector      │   │  Holdings    │   │  OAuth       │     │
    │  │  (KPIs)      │   │  Allocation  │   │  Table       │   │  Login Flow  │     │
    │  │              │   │  (Charts)    │   │              │   │              │     │
    │  └──────┬───────┘   └──────┬───────┘   └──────┬───────┘   └──────┬───────┘     │
    │         │                  │                   │                  │              │
    │         └──────────────────┴───────────────────┴──────────────────┘              │
    │                                    │                                             │
    │                          HTMX (hx-get, hx-swap)                                 │
    │                          + Chart.js (sector pie/bar)                             │
    └────────────────────────────────────┬────────────────────────────────────────────┘
                                         │
                                         │ HTTP/REST
                                         │ (JSON + HTML partials)
    ┌────────────────────────────────────▼────────────────────────────────────────────┐
    │                          FASTAPI BACKEND                                        │
    │                                                                                 │
    │  ┌─────────────────────────────────────────────────────────────────────────┐    │
    │  │                        ROUTES LAYER                                     │    │
    │  │  /auth/zerodha/*  │  /api/portfolio/*  │  /api/sectors/*  │  /health    │    │
    │  └─────────────────────────────┬───────────────────────────────────────────┘    │
    │                                │                                                │
    │  ┌─────────────────────────────▼───────────────────────────────────────────┐    │
    │  │                      SERVICES LAYER                                     │    │
    │  │  AuthService  │  HoldingsService  │  SectorService  │  SnapshotService  │    │
    │  └─────────────────────────────┬───────────────────────────────────────────┘    │
    │                                │                                                │
    │  ┌─────────────────────────────▼───────────────────────────────────────────┐    │
    │  │                      DATA ACCESS LAYER (SQLModel)                       │    │
    │  │  SessionRepo  │  HoldingRepo  │  InstrumentRepo  │  SnapshotRepo       │    │
    │  └─────────────────────────────┬───────────────────────────────────────────┘    │
    │                                │                                                │
    │  ┌─────────────────────────────▼───────────────────────────────────────────┐    │
    │  │                      SCHEDULER (APScheduler)                             │    │
    │  │  SOD Snapshot (9:00 AM IST)  │  EOD Snapshot (4:45 PM IST)              │    │
    │  └─────────────────────────────────────────────────────────────────────────┘    │
    │                                                                                 │
    └────────────────────┬──────────────────────────────┬─────────────────────────────┘
                         │                              │
                         ▼                              ▼
    ┌────────────────────────────────┐   ┌──────────────────────────────────────────┐
    │      EXTERNAL APIS             │   │         DATA STORE                       │
    │                                │   │                                          │
    │  ┌──────────────────────────┐  │   │  ┌──────────────────────────────────┐    │
    │  │  Zerodha Kite API        │  │   │  │  SQLite (MVP)                    │    │
    │  │  • OAuth token exchange  │  │   │  │  → PostgreSQL (Growth)           │    │
    │  │  • GET /portfolio/hold.  │  │   │  │                                  │    │
    │  └──────────────────────────┘  │   │  │  Tables:                         │    │
    │                                │   │  │  • sessions                      │    │
    │  ┌──────────────────────────┐  │   │  │  • holdings_snapshots            │    │
    │  │  Yahoo Finance API       │  │   │  │  • instruments                   │    │
    │  │  • Sector/Industry data  │  │   │  │  • sector_allocations (derived)  │    │
    │  └──────────────────────────┘  │   │  └──────────────────────────────────┘    │
    │                                │   │                                          │
    └────────────────────────────────┘   └──────────────────────────────────────────┘
```

---

## 3. Low-Level Architecture (LLA)

### 3.1 Backend — Module Dependency Graph

```
                            LOW-LEVEL ARCHITECTURE — BACKEND
                            ═════════════════════════════════

    main.py
    ├── Registers middleware (CORS, error handlers)
    ├── Registers routers
    ├── Initializes DB (via lifespan)
    └── Starts APScheduler

    ┌──────────────────────────────────────────────────────────────────────────┐
    │                           ROUTES (FastAPI Routers)                       │
    │                                                                          │
    │  auth/                          api/                                     │
    │  └── zerodha_router.py          ├── portfolio_router.py                  │
    │      GET /login                 │   GET /api/portfolio/overview          │
    │      GET /callback              │   GET /api/portfolio/holdings          │
    │                                 │                                        │
    │                                 ├── sector_router.py                     │
    │                                 │   GET /api/sectors/allocation          │
    │                                 │   GET /api/sectors/{name}/holdings     │
    │                                 │                                        │
    │                                 └── snapshot_router.py                   │
    │                                     GET /api/snapshots/latest            │
    │                                     POST /api/snapshots/trigger          │
    └──────────────────┬───────────────────────────────────────────────────────┘
                       │ depends on
    ┌──────────────────▼───────────────────────────────────────────────────────┐
    │                           SERVICES (Business Logic)                      │
    │                                                                          │
    │  ┌─────────────────────┐  ┌─────────────────────┐                       │
    │  │  auth_service.py    │  │  holdings_service.py │                       │
    │  │                     │  │                      │                       │
    │  │  • get_login_url()  │  │  • fetch_live()      │                       │
    │  │  • exchange_token() │  │  • get_enriched()    │                       │
    │  │  • validate_sess()  │  │  • compute_kpis()    │                       │
    │  └─────────────────────┘  └──────────┬───────────┘                       │
    │                                      │ calls                             │
    │  ┌─────────────────────┐  ┌──────────▼───────────┐                       │
    │  │  sector_service.py  │  │  snapshot_service.py  │                       │
    │  │                     │  │                       │                       │
    │  │  • get_allocation() │  │  • take_snapshot()    │                       │
    │  │  • enrich_symbol()  │  │  • get_latest()       │                       │
    │  │  • get_sector_hldg()│  │  • is_within_window() │                       │
    │  └─────────┬───────────┘  └───────────────────────┘                       │
    │            │ calls                                                        │
    │  ┌─────────▼───────────┐                                                 │
    │  │  yahoo_client.py    │  (External API wrapper)                         │
    │  │  • fetch_info()     │                                                 │
    │  │  • _to_yf_symbol()  │                                                 │
    │  └─────────────────────┘                                                 │
    └──────────────────┬───────────────────────────────────────────────────────┘
                       │ depends on
    ┌──────────────────▼───────────────────────────────────────────────────────┐
    │                     DATA ACCESS LAYER (SQLModel + Repositories)          │
    │                                                                          │
    │  database.py                                                             │
    │  ├── engine = create_engine("sqlite:///data/tunefolio.db")               │
    │  ├── get_session() → yields SQLModel Session                             │
    │  └── init_db() → SQLModel.metadata.create_all(engine)                    │
    │                                                                          │
    │  models/                                                                 │
    │  ├── session.py      → ZerodhaSession(SQLModel, table=True)              │
    │  ├── instrument.py   → Instrument(SQLModel, table=True)                  │
    │  ├── snapshot.py     → HoldingsSnapshot(SQLModel, table=True)            │
    │  └── schemas.py      → HoldingResponse, KPIResponse, SectorAllocation    │
    │                        (Pydantic models for API responses)                │
    │                                                                          │
    │  repos/                                                                  │
    │  ├── session_repo.py    → save(), get_active(), invalidate()             │
    │  ├── instrument_repo.py → upsert(), get_by_symbol(), get_all_sectors()   │
    │  ├── snapshot_repo.py   → save_batch(), get_latest(), exists_today()     │
    │  └── holding_repo.py    → (thin wrapper, holdings are fetched live)      │
    └──────────────────────────────────────────────────────────────────────────┘
```

### 3.2 Frontend — Component Architecture

```
                         LOW-LEVEL ARCHITECTURE — FRONTEND
                         ══════════════════════════════════

    templates/                            static/
    ├── base.html                         ├── css/
    │   ├── <head> (Chart.js, HTMX CDN)  │   └── main.css (dark theme vars)
    │   ├── <nav> (brand, status)         │
    │   └── {% block content %}           ├── js/
    │                                     │   ├── charts.js (Chart.js init)
    ├── dashboard.html                    │   └── utils.js  (formatINR, etc.)
    │   ├── KPI strip (3 cards)           │
    │   ├── Holdings table                └── img/
    │   ├── Sector Allocation charts          └── (empty for now)
    │   └── Each section uses
    │       hx-get="/api/..." hx-trigger="load"
    │
    ├── partials/                     ← HTMX swaps these in
    │   ├── _kpi_strip.html           ← rendered by /api/portfolio/overview
    │   ├── _holdings_table.html      ← rendered by /api/portfolio/holdings
    │   ├── _sector_pie.html          ← rendered by /api/sectors/allocation
    │   └── _sector_details.html      ← rendered by /api/sectors/{name}
    │
    ├── auth/
    │   ├── login.html                ← "Connect with Zerodha" button
    │   └── success.html              ← OAuth callback landing
    │
    └── errors/
        ├── 401.html                  ← Session expired
        └── 500.html                  ← Server error
```

### 3.3 Request Flow — Sequence Diagram

```
    ┌────────┐          ┌──────────┐        ┌──────────┐       ┌─────────┐     ┌────────┐
    │Browser │          │ FastAPI  │        │ Services │       │  Repos  │     │External│
    │(HTMX)  │          │ Routes   │        │          │       │(SQLModel│     │  APIs   │
    └───┬────┘          └────┬─────┘        └────┬─────┘       └────┬────┘     └───┬────┘
        │                    │                   │                  │               │
        │ GET /dashboard     │                   │                  │               │
        │───────────────────>│                   │                  │               │
        │ <full page HTML>   │                   │                  │               │
        │<───────────────────│                   │                  │               │
        │                    │                   │                  │               │
        │ hx-get="/api/portfolio/holdings"       │                  │               │
        │───────────────────>│                   │                  │               │
        │                    │ get_enriched()    │                  │               │
        │                    │──────────────────>│                  │               │
        │                    │                   │ get_active_token │               │
        │                    │                   │─────────────────>│               │
        │                    │                   │ <token>          │               │
        │                    │                   │<─────────────────│               │
        │                    │                   │                  │               │
        │                    │                   │ GET /portfolio/holdings           │
        │                    │                   │─────────────────────────────────>│
        │                    │                   │ <raw holdings JSON>              │
        │                    │                   │<─────────────────────────────────│
        │                    │                   │                  │               │
        │                    │                   │ upsert_instruments               │
        │                    │                   │─────────────────>│               │
        │                    │                   │                  │               │
        │                    │                   │ enrich_missing_sectors            │
        │                    │                   │─────────────────>│ (check cache) │
        │                    │                   │                  │───────────────>│
        │                    │                   │                  │ <sector data>  │
        │                    │                   │                  │<───────────────│
        │                    │                   │                  │               │
        │                    │                   │ save_snapshot()  │               │
        │                    │                   │─────────────────>│               │
        │                    │                   │                  │               │
        │                    │ <enriched data>   │                  │               │
        │                    │<──────────────────│                  │               │
        │                    │                   │                  │               │
        │ <HTML partial>     │ render _holdings_table.html          │               │
        │<───────────────────│                   │                  │               │
        │                    │                   │                  │               │
        │ hx-get="/api/sectors/allocation"       │                  │               │
        │───────────────────>│                   │                  │               │
        │                    │ get_allocation()  │                  │               │
        │                    │──────────────────>│                  │               │
        │                    │                   │ aggregate by sector              │
        │                    │                   │─────────────────>│               │
        │                    │                   │ <sector groups>  │               │
        │                    │                   │<─────────────────│               │
        │                    │ <chart data HTML> │                  │               │
        │<───────────────────│                   │                  │               │
        │                    │                   │                  │               │
        │ Chart.js renders   │                   │                  │               │
        │ pie + bar charts   │                   │                  │               │
        │                    │                   │                  │               │
```

---

## 4. Database Schema & Data Models

### 4.1 Entity Relationship Diagram

```
    ┌─────────────────────────┐       ┌─────────────────────────────────────┐
    │    zerodha_sessions     │       │         instruments                 │
    ├─────────────────────────┤       ├─────────────────────────────────────┤
    │ PK  id          UUID    │       │ PK  symbol       VARCHAR(20)       │
    │     user_id     VARCHAR │       │ PK  exchange     VARCHAR(5)        │
    │     access_token VARCHAR│       │     company_name VARCHAR(100)      │
    │     created_at  DATETIME│       │     sector       VARCHAR(50)       │
    │     expires_at  DATETIME│       │     industry     VARCHAR(100)      │
    │     is_active   BOOLEAN │       │     isin         VARCHAR(12)       │
    └─────────────────────────┘       │     created_at   DATETIME          │
                                      │     updated_at   DATETIME          │
                                      └──────────┬──────────────────────────┘
                                                 │ 1
                                                 │
                                                 │ symbol + exchange
                                                 │
                                                 │ N
    ┌────────────────────────────────────────────┴──────────────────────────┐
    │                     holdings_snapshots                                │
    ├──────────────────────────────────────────────────────────────────────┤
    │ PK  id              UUID                                             │
    │     snapshot_at      DATETIME (IST)                                  │
    │     snapshot_type    ENUM('SOD', 'EOD', 'MANUAL')                    │
    │ FK  tradingsymbol    VARCHAR(20)  → instruments.symbol               │
    │ FK  exchange         VARCHAR(5)   → instruments.exchange              │
    │     quantity         INTEGER                                         │
    │     average_price    REAL                                            │
    │     last_price       REAL                                            │
    │     pnl              REAL                                            │
    │                                                                      │
    │ UNIQUE(tradingsymbol, exchange, snapshot_type,                        │
    │        DATE(snapshot_at))                                             │
    └──────────────────────────────────────────────────────────────────────┘
```

### 4.2 SQLModel Definitions

```python
# models/session.py
class ZerodhaSession(SQLModel, table=True):
    __tablename__ = "zerodha_sessions"

    id: str             = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    user_id: str
    access_token: str                               # TODO: encrypt at rest
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime
    is_active: bool     = Field(default=True)


# models/instrument.py
class Instrument(SQLModel, table=True):
    __tablename__ = "instruments"

    symbol: str       = Field(primary_key=True, max_length=20)
    exchange: str     = Field(primary_key=True, max_length=5)
    company_name: str | None = None
    sector: str | None       = None
    industry: str | None     = None
    isin: str | None         = None
    created_at: datetime     = Field(default_factory=datetime.utcnow)
    updated_at: datetime | None = None


# models/snapshot.py
class HoldingsSnapshot(SQLModel, table=True):
    __tablename__ = "holdings_snapshots"

    id: str             = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    snapshot_at: datetime
    snapshot_type: str  # 'SOD' | 'EOD' | 'MANUAL'
    tradingsymbol: str
    exchange: str | None = None
    quantity: int | None = None
    average_price: float | None = None
    last_price: float | None    = None
    pnl: float | None           = None
```

### 4.3 Pydantic Response Schemas

```python
# models/schemas.py
class HoldingResponse(BaseModel):
    symbol: str
    exchange: str
    sector: str | None
    industry: str | None
    quantity: int
    avg_buy_price: float
    current_price: float
    invested_value: float
    current_value: float
    pnl: float
    pnl_pct: float           # NEW: percentage P&L
    last_snapshot_at: str | None
    snapshot_count: int


class KPIResponse(BaseModel):
    total_stocks: int
    total_invested: float
    current_value: float
    total_pnl: float
    total_pnl_pct: float     # NEW: overall % return


class SectorAllocation(BaseModel):
    sector: str
    stock_count: int
    invested_value: float
    current_value: float
    pnl: float
    pnl_pct: float
    weight_pct: float         # % of total portfolio


class SectorHolding(BaseModel):
    symbol: str
    quantity: int
    invested_value: float
    current_value: float
    pnl: float
    pnl_pct: float
```

---

## 5. API Contracts

### 5.1 Endpoint Map

```
    METHOD   PATH                              RESPONSE         AUTH
    ─────────────────────────────────────────────────────────────────
    GET      /                                 HealthCheck      No
    GET      /auth/zerodha/login               302 → Zerodha    No
    GET      /auth/zerodha/callback            302 → /dashboard Yes*
    GET      /dashboard                        HTML page        Session
    ─────────────────────────────────────────────────────────────────
    GET      /api/portfolio/overview            KPIResponse      Session
    GET      /api/portfolio/holdings            HoldingResponse[]Session
    ─────────────────────────────────────────────────────────────────
    GET      /api/sectors/allocation            SectorAllocation[] Session
    GET      /api/sectors/{sector}/holdings     SectorHolding[]  Session
    ─────────────────────────────────────────────────────────────────
    GET      /api/snapshots/latest              SnapshotMeta     Session
    POST     /api/snapshots/trigger             SnapshotResult   Session
    ─────────────────────────────────────────────────────────────────
    GET      /api/session/active                SessionInfo      Session
    ─────────────────────────────────────────────────────────────────
```

### 5.2 Key Response Shapes

**GET /api/portfolio/overview**
```json
{
  "total_stocks": 24,
  "total_invested": 485230.50,
  "current_value": 521045.75,
  "total_pnl": 35815.25,
  "total_pnl_pct": 7.38
}
```

**GET /api/portfolio/holdings**
```json
{
  "count": 24,
  "data": [
    {
      "symbol": "TATAPOWER",
      "exchange": "NSE",
      "sector": "Utilities",
      "industry": "Power Generation",
      "quantity": 50,
      "avg_buy_price": 245.30,
      "current_price": 268.15,
      "invested_value": 12265.00,
      "current_value": 13407.50,
      "pnl": 1142.50,
      "pnl_pct": 9.31,
      "last_snapshot_at": "2026-02-12T09:00:00+05:30",
      "snapshot_count": 45
    }
  ]
}
```

**GET /api/sectors/allocation** (NEW — core MVP endpoint)
```json
{
  "total_portfolio_value": 521045.75,
  "sectors": [
    {
      "sector": "Utilities",
      "stock_count": 4,
      "invested_value": 98500.00,
      "current_value": 107230.50,
      "pnl": 8730.50,
      "pnl_pct": 8.86,
      "weight_pct": 20.58
    },
    {
      "sector": "Financials",
      "stock_count": 5,
      "invested_value": 125000.00,
      "current_value": 118750.00,
      "pnl": -6250.00,
      "pnl_pct": -5.00,
      "weight_pct": 22.79
    }
  ]
}
```

**GET /api/sectors/{sector}/holdings** (NEW — drill-down)
```json
{
  "sector": "Utilities",
  "stock_count": 4,
  "holdings": [
    {
      "symbol": "TATAPOWER",
      "quantity": 50,
      "invested_value": 12265.00,
      "current_value": 13407.50,
      "pnl": 1142.50,
      "pnl_pct": 9.31
    }
  ]
}
```

---

## 6. Frontend Component Architecture

### 6.1 Dashboard Layout

```
    ┌────────────────────────────────────────────────────────────────────────┐
    │  HEADER                                                                │
    │  ┌──────────────────┐                    ┌───────────────────────────┐ │
    │  │ TuneFolio        │                    │ ● Connected │ Last: 9:01 │ │
    │  │ Portfolio Intel.  │                    └───────────────────────────┘ │
    │  └──────────────────┘                                                  │
    ├────────────────────────────────────────────────────────────────────────┤
    │  KPI STRIP  (hx-get="/api/portfolio/overview" hx-trigger="load")       │
    │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌───────────┐ │
    │  │ Total Value   │  │  Invested    │  │  Total P&L   │  │ Return %  │ │
    │  │ ₹5,21,046    │  │  ₹4,85,231  │  │ +₹35,815    │  │  +7.38%   │ │
    │  │              │  │              │  │  (green)     │  │  (green)  │ │
    │  └──────────────┘  └──────────────┘  └──────────────┘  └───────────┘ │
    ├────────────────────────────────────────────────────────────────────────┤
    │  SECTOR ALLOCATION  (hx-get="/api/sectors/allocation" hx-trigger="load")│
    │  ┌──────────────────────────────┐  ┌──────────────────────────────┐   │
    │  │                              │  │                              │   │
    │  │      PIE / DOUGHNUT          │  │       HORIZONTAL BAR         │   │
    │  │      (Weight %)              │  │       (P&L by Sector)        │   │
    │  │                              │  │                              │   │
    │  │   ┌───┐                      │  │  Utilities   ████████ +8.9%  │   │
    │  │   │   │ Financials 22.8%     │  │  Financials  ████     -5.0%  │   │
    │  │   │   │ Utilities  20.6%     │  │  Technology  ██████   +6.2%  │   │
    │  │   │   │ Technology 15.3%     │  │  Materials   ███      +2.1%  │   │
    │  │   │   │ Materials  12.1%     │  │  Energy      ████████ +11.3% │   │
    │  │   └───┘ ...                  │  │  ...                         │   │
    │  │                              │  │                              │   │
    │  └──────────────────────────────┘  └──────────────────────────────┘   │
    ├────────────────────────────────────────────────────────────────────────┤
    │  HOLDINGS TABLE  (hx-get="/api/portfolio/holdings" hx-trigger="load")  │
    │  ┌────────────────────────────────────────────────────────────────┐   │
    │  │ Stock   Qty  Avg Buy   LTP    Invested    Value    P&L  Sector│   │
    │  │─────────────────────────────────────────────────────────────── │   │
    │  │ TATAPWR  50  ₹245.30  ₹268   ₹12,265  ₹13,408  +₹1,143 Util│   │
    │  │ BEL     100  ₹180.00  ₹195   ₹18,000  ₹19,500  +₹1,500 Ind │   │
    │  │ BSE      10  ₹2100    ₹1980  ₹21,000  ₹19,800   -₹1,200 Fin│   │
    │  │ ...                                                           │   │
    │  └────────────────────────────────────────────────────────────────┘   │
    │                                                                        │
    │  Click sector name → hx-get="/api/sectors/{name}/holdings"             │
    │  → swaps in sector drill-down below the chart                          │
    └────────────────────────────────────────────────────────────────────────┘
```

### 6.2 Chart.js Integration

```javascript
// static/js/charts.js

// Sector Allocation — Doughnut Chart
function renderSectorPie(canvasId, sectors) {
    new Chart(document.getElementById(canvasId), {
        type: 'doughnut',
        data: {
            labels: sectors.map(s => s.sector),
            datasets: [{
                data: sectors.map(s => s.weight_pct),
                backgroundColor: [
                    '#6366f1', '#22c55e', '#f59e0b',
                    '#ef4444', '#06b6d4', '#8b5cf6',
                    '#ec4899', '#14b8a6', '#f97316'
                ]
            }]
        },
        options: {
            responsive: true,
            plugins: {
                legend: { position: 'right', labels: { color: '#e5e7eb' } },
                tooltip: {
                    callbacks: {
                        label: (ctx) => `${ctx.label}: ${ctx.parsed}% (₹${ctx.raw})`
                    }
                }
            }
        }
    });
}

// Sector P&L — Horizontal Bar
function renderSectorPnL(canvasId, sectors) {
    new Chart(document.getElementById(canvasId), {
        type: 'bar',
        data: {
            labels: sectors.map(s => s.sector),
            datasets: [{
                label: 'P&L %',
                data: sectors.map(s => s.pnl_pct),
                backgroundColor: sectors.map(s =>
                    s.pnl_pct >= 0 ? '#16a34a' : '#dc2626'
                )
            }]
        },
        options: {
            indexAxis: 'y',
            responsive: true,
            scales: {
                x: { ticks: { color: '#9ca3af' }, grid: { color: '#1f2933' } },
                y: { ticks: { color: '#e5e7eb' }, grid: { display: false } }
            },
            plugins: {
                legend: { display: false }
            }
        }
    });
}
```

---

## 7. Authentication Flow

```
    ┌──────────┐                 ┌──────────┐                  ┌──────────┐
    │  Browser  │                 │  FastAPI  │                  │  Zerodha │
    └─────┬────┘                 └─────┬────┘                  └─────┬────┘
          │                            │                             │
          │  1. Click "Connect"        │                             │
          │───────────────────────────>│                             │
          │                            │                             │
          │  2. 302 Redirect           │                             │
          │<───────────────────────────│                             │
          │     Location: kite.trade/connect/login?api_key=XXX       │
          │                            │                             │
          │  3. User logs in at Zerodha│                             │
          │─────────────────────────────────────────────────────────>│
          │                            │                             │
          │  4. 302 Redirect           │                             │
          │<─────────────────────────────────────────────────────────│
          │     /auth/zerodha/callback?request_token=YYY&status=success
          │                            │                             │
          │  5. Callback hits FastAPI  │                             │
          │───────────────────────────>│                             │
          │                            │  6. SHA256(api_key +        │
          │                            │     request_token +         │
          │                            │     api_secret) = checksum  │
          │                            │                             │
          │                            │  7. POST /session/token     │
          │                            │───────────────────────────>│
          │                            │                             │
          │                            │  8. { access_token, user }  │
          │                            │<───────────────────────────│
          │                            │                             │
          │                            │  9. Save to DB:             │
          │                            │     zerodha_sessions table  │
          │                            │     (12h expiry)            │
          │                            │                             │
          │  10. 302 → /dashboard      │                             │
          │<───────────────────────────│                             │
          │                            │                             │
          │  11. Dashboard loads       │                             │
          │  HTMX triggers API calls   │                             │
          │───────────────────────────>│                             │
          │                            │                             │
```

---

## 8. Data Pipeline

### Holdings → Sectors → Visualization

```
    ┌──────────────────────────────────────────────────────────────────────┐
    │                     DATA PIPELINE FLOW                               │
    └──────────────────────────────────────────────────────────────────────┘

    STEP 1: FETCH LIVE HOLDINGS
    ┌──────────────┐     GET /portfolio/holdings      ┌──────────────────┐
    │  FastAPI      │ ──────────────────────────────> │  Zerodha API     │
    │  Backend      │ <────────────────────────────── │                  │
    │               │     [{tradingsymbol, exchange,  │  Returns raw     │
    │               │       quantity, average_price,   │  holdings JSON   │
    │               │       last_price, pnl, isin}]   └──────────────────┘
    └──────┬───────┘
           │
    STEP 2: UPSERT INSTRUMENTS
           │  For each holding:
           │  INSERT OR IGNORE INTO instruments (symbol, exchange, isin)
           ▼
    ┌──────────────┐
    │  instruments  │  symbol + exchange → composite PK
    │  table        │  sector = NULL (initially)
    └──────┬───────┘
           │
    STEP 3: ENRICH SECTORS (LAZY, CACHED)
           │  For each instrument WHERE sector IS NULL:
           │
           │  ┌─────────────────────────────────────────┐
           │  │  1. Check instruments table (cache hit?) │
           │  │     → If sector exists, skip            │
           │  │                                         │
           │  │  2. Call Yahoo Finance:                  │
           │  │     yf.Ticker("SYMBOL.NS").info         │
           │  │     → Extract sector, industry          │
           │  │                                         │
           │  │  3. Fallback: sector_map.py             │
           │  │     → Static map for known symbols      │
           │  │                                         │
           │  │  4. UPDATE instruments SET sector=?,     │
           │  │     industry=? WHERE symbol=?            │
           │  └─────────────────────────────────────────┘
           ▼
    STEP 4: AGGREGATE BY SECTOR
    ┌──────────────────────────────────────────────────────────────────┐
    │  SELECT                                                          │
    │    i.sector,                                                     │
    │    COUNT(DISTINCT h.tradingsymbol)  AS stock_count,              │
    │    SUM(h.average_price * h.quantity) AS invested_value,          │
    │    SUM(h.last_price * h.quantity)    AS current_value,           │
    │    SUM(h.pnl)                        AS pnl                     │
    │  FROM live_holdings h                                            │
    │  JOIN instruments i ON h.tradingsymbol = i.symbol                │
    │  GROUP BY i.sector                                               │
    │  ORDER BY current_value DESC                                     │
    └──────────────────────────────────────────────────────────────────┘
           │
           ▼
    STEP 5: COMPUTE DERIVED METRICS
    ┌──────────────────────────────────────────────────────────────────┐
    │  For each sector:                                                │
    │    pnl_pct    = (current_value - invested_value) / invested × 100│
    │    weight_pct = current_value / total_portfolio_value × 100      │
    └──────────────────────────────────────────────────────────────────┘
           │
           ▼
    STEP 6: RENDER VISUALIZATIONS
    ┌──────────────────────────────────────────────────────────────────┐
    │                                                                  │
    │  ┌─────────────────────┐    ┌─────────────────────────────────┐ │
    │  │   DOUGHNUT CHART     │    │   HORIZONTAL BAR CHART          │ │
    │  │   (Sector Weights)   │    │   (Sector P&L %)               │ │
    │  │                     │    │                                 │ │
    │  │   Financials 22.8%  │    │   Utilities   ████████  +8.9%  │ │
    │  │   Utilities  20.6%  │    │   Technology  ██████    +6.2%  │ │
    │  │   Technology 15.3%  │    │   Financials  ████      -5.0%  │ │
    │  │   ...               │    │   ...                          │ │
    │  └─────────────────────┘    └─────────────────────────────────┘ │
    │                                                                  │
    │  ┌──────────────────────────────────────────────────────────────┐│
    │  │   SECTOR DETAIL TABLE (click to drill down)                  ││
    │  │   Sector: Utilities                                          ││
    │  │   ┌────────┬─────┬──────────┬──────────┬────────┬───────┐  ││
    │  │   │ Stock  │ Qty │ Invested │  Value   │  P&L   │  P&L% │  ││
    │  │   │TATAPWR │  50 │ ₹12,265  │ ₹13,408  │+₹1,143│ +9.3% │  ││
    │  │   │JSWENRG │  30 │ ₹15,600  │ ₹16,920  │+₹1,320│ +8.5% │  ││
    │  │   └────────┴─────┴──────────┴──────────┴────────┴───────┘  ││
    │  └──────────────────────────────────────────────────────────────┘│
    └──────────────────────────────────────────────────────────────────┘
```

---

## 9. Deployment Architecture

```
                         DEPLOYMENT ARCHITECTURE
                         ═══════════════════════

    ┌──────────────────────────────────────────────────────────────┐
    │                      GITHUB REPOSITORY                       │
    │                                                              │
    │  push to main ──────────┐                                    │
    │                         │                                    │
    │  ┌──────────────────────▼─────────────────────────────────┐  │
    │  │             GITHUB ACTIONS CI/CD                        │  │
    │  │                                                        │  │
    │  │  ┌────────────┐  ┌────────────┐  ┌─────────────────┐  │  │
    │  │  │ ruff check │  │ mypy       │  │ pytest          │  │  │
    │  │  │ ruff format│  │ --strict   │  │ --cov=80%       │  │  │
    │  │  └─────┬──────┘  └─────┬──────┘  └──────┬──────────┘  │  │
    │  │        │               │                │              │  │
    │  │        └───────────────┴────────────────┘              │  │
    │  │                        │                               │  │
    │  │                   All pass?                             │  │
    │  │                    YES │                                │  │
    │  │                        ▼                               │  │
    │  │              ┌─────────────────┐                       │  │
    │  │              │ docker build    │                       │  │
    │  │              │ docker push     │                       │  │
    │  │              └────────┬────────┘                       │  │
    │  └───────────────────────┼────────────────────────────────┘  │
    └──────────────────────────┼───────────────────────────────────┘
                               │
                               ▼  Auto-deploy
    ┌──────────────────────────────────────────────────────────────┐
    │                   RENDER / RAILWAY                            │
    │                                                              │
    │  ┌──────────────────────────────────────────────────────┐   │
    │  │  Docker Container                                     │   │
    │  │                                                       │   │
    │  │  ┌─────────────────────────────────────────────────┐  │   │
    │  │  │  Uvicorn (port 8000)                            │  │   │
    │  │  │  ├── FastAPI App                                │  │   │
    │  │  │  │   ├── Auth routes                            │  │   │
    │  │  │  │   ├── API routes                             │  │   │
    │  │  │  │   └── Template routes (Jinja2)               │  │   │
    │  │  │  │                                              │  │   │
    │  │  │  └── APScheduler (in-process)                   │  │   │
    │  │  │      ├── SOD job: 9:00 AM IST                   │  │   │
    │  │  │      └── EOD job: 4:45 PM IST                   │  │   │
    │  │  └─────────────────────────────────────────────────┘  │   │
    │  │                                                       │   │
    │  │  ┌─────────────────┐                                  │   │
    │  │  │ data/            │  (Persistent disk on paid plan)  │   │
    │  │  │ └─tunefolio.db  │  SQLite file                     │   │
    │  │  └─────────────────┘                                  │   │
    │  └───────────────────────────────────────────────────────┘   │
    │                                                              │
    └──────────────────────────────────────────────────────────────┘

    Dockerfile:
    ┌──────────────────────────────────────────────┐
    │  FROM python:3.11-slim                       │
    │  WORKDIR /app                                │
    │  COPY pyproject.toml uv.lock ./              │
    │  RUN pip install uv && uv sync --frozen      │
    │  COPY . .                                    │
    │  EXPOSE 8000                                 │
    │  CMD ["uvicorn", "backend.app.main:app",     │
    │       "--host", "0.0.0.0", "--port", "8000"] │
    └──────────────────────────────────────────────┘
```

---

## 10. Testing Strategy

```
    ┌──────────────────────────────────────────────────────────────────┐
    │                      TESTING PYRAMID                             │
    │                                                                  │
    │                          ╱╲                                      │
    │                         ╱  ╲       E2E (manual for MVP)          │
    │                        ╱    ╲      - Zerodha OAuth flow          │
    │                       ╱──────╲     - Dashboard renders           │
    │                      ╱        ╲                                  │
    │                     ╱ Integr.  ╲   Integration (httpx + TestClient)
    │                    ╱            ╲  - API endpoints return correct │
    │                   ╱              ╲   shapes and status codes     │
    │                  ╱────────────────╲ - DB operations with test DB │
    │                 ╱                  ╲                              │
    │                ╱    Unit Tests      ╲  Unit (pytest)              │
    │               ╱                      ╲ - Sector aggregation logic│
    │              ╱                        ╲- KPI calculations        │
    │             ╱                          ╲- Snapshot dedup logic   │
    │            ╱                            ╲- Yahoo symbol conversion│
    │           ╱──────────────────────────────╲                       │
    │                                                                  │
    └──────────────────────────────────────────────────────────────────┘

    Key test files:
    tests/
    ├── conftest.py          → Fixtures: test DB, mock Zerodha, mock Yahoo
    ├── test_kpi.py          → KPI calculation accuracy
    ├── test_sectors.py      → Sector aggregation, weight %, P&L %
    ├── test_snapshots.py    → SOD/EOD dedup, time window logic
    ├── test_instruments.py  → Yahoo symbol conversion, enrichment
    └── test_api.py          → Endpoint status codes, response shapes
```

---

## 11. Migration Path from Current Codebase

### Phase-by-Phase Plan

```
    CURRENT STATE                           TARGET STATE (MVP)
    ═════════════                           ══════════════════

    backend/
    ├── app/
    │   ├── main.py ──────────────────────> main.py (lifespan, clean routers)
    │   ├── auth/
    │   │   └── zerodha.py ───────────────> auth/zerodha_router.py
    │   ├── routes/
    │   │   └── portfolio.py ─────────────> routes/portfolio_router.py
    │   │                                   routes/sector_router.py     [NEW]
    │   │                                   routes/snapshot_router.py   [NEW]
    │   └── services/
    │       ├── db.py (327 lines!) ───────> database.py (engine + session)
    │       │                               models/session.py   (SQLModel)
    │       │                               models/instrument.py(SQLModel)
    │       │                               models/snapshot.py  (SQLModel)
    │       │                               models/schemas.py   (Pydantic)
    │       │                               repos/session_repo.py
    │       │                               repos/instrument_repo.py
    │       │                               repos/snapshot_repo.py
    │       ├── zerodha_holdings.py ──────> services/holdings_service.py
    │       ├── instruments.py ───────────> services/sector_service.py
    │       ├── sector_map.py ────────────> services/sector_map.py (keep)
    │       ├── holdings.py ──────────────> (merged into portfolio_router)
    │       └── sessions.py ──────────────> (merged into auth_router)

    frontend/
    ├── index.html (inline JS) ──────────> templates/dashboard.html (Jinja2)
    │                                       templates/partials/_kpi_strip.html
    │                                       templates/partials/_holdings.html
    │                                       templates/partials/_sector_pie.html
    ├── success.html ─────────────────────> templates/auth/success.html
    └── assets/css/main.css ──────────────> static/css/main.css (keep)
                                            static/js/charts.js             [NEW]

    NEW FILES:
    ├── pyproject.toml                      (replaces requirements.txt)
    ├── Dockerfile
    ├── .github/workflows/ci.yml
    ├── tests/conftest.py
    ├── tests/test_kpi.py
    ├── tests/test_sectors.py
    └── tests/test_api.py
```

### Migration Priority Order

```
    Priority   What                                      Why
    ────────   ────                                      ───
    P0         Fix main.py duplicate registrations       Bug in current code
    P0         Add pyproject.toml + Ruff + Mypy          Foundation for quality
    P1         Replace raw sqlite3 with SQLModel         Type safety + migrations
    P1         Add /api/sectors/allocation endpoint       Core MVP feature
    P1         Add Chart.js sector visualizations         Core MVP feature
    P2         Add APScheduler for SOD/EOD                Automate snapshots
    P2         Add pytest + httpx tests                   Safety net
    P2         Migrate frontend to Jinja2 + HTMX          Better DX
    P3         Add Dockerfile + CI/CD                      Deployment readiness
    P3         Encrypt access tokens at rest               Security hardening
```

---

## Summary

This architecture transforms TuneFolio from a weekend prototype into a production-ready MVP while keeping costs at $0-5/month. The key principles:

1. **SQLModel** — One model definition serves API validation AND database persistence
2. **Jinja2 + HTMX** — Server-rendered, zero-build frontend with dynamic updates
3. **Chart.js** — Lightweight sector allocation visualizations (pie + bar)
4. **APScheduler** — In-process SOD/EOD snapshots, no Redis/Celery overhead
5. **Repository pattern** — Clean separation replaces the 327-line `db.py` god module
6. **pytest + httpx** — Test financial calculations before they hit production
7. **Ruff + Mypy** — Catch bugs at write-time, not runtime

The MVP scope is deliberately narrow: **login → fetch holdings → enrich sectors → visualize allocations**. Everything else (trading journal, time-series, multi-user) is deferred.
