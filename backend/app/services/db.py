import sqlite3
import shutil
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "tunefolio.db"
SEED_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "tunefolio.seed.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)  # Ensure data/ dir exists (for Render deploys)

# On Render (ephemeral filesystem), restore from seed if DB doesn't exist
if not DB_PATH.exists() and SEED_DB_PATH.exists():
    shutil.copy2(SEED_DB_PATH, DB_PATH)

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS zerodha_sessions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            access_token TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            is_active INTEGER DEFAULT 1
        )
    """)

    conn.commit()
    conn.close()

import uuid
from datetime import datetime, timedelta

def save_zerodha_session(user_id: str, access_token: str):
    conn = get_connection()
    cursor = conn.cursor()

    session_id = str(uuid.uuid4())
    created_at = datetime.utcnow()
    expires_at = created_at + timedelta(hours=12)  # Zerodha token validity (approx)

    cursor.execute("""
        INSERT INTO zerodha_sessions (
            id, user_id, access_token, created_at, expires_at, is_active
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        session_id,
        user_id,
        access_token,
        created_at.isoformat(),
        expires_at.isoformat(),
        1
    ))

    conn.commit()
    conn.close()

    return session_id

def get_active_zerodha_session(session_id: str = None):
    """Look up an active session by its cookie session_id."""
    conn = get_connection()
    cursor = conn.cursor()

    if session_id:
        cursor.execute("""
            SELECT id, user_id, created_at, expires_at
            FROM zerodha_sessions
            WHERE id = ? AND is_active = 1
            LIMIT 1
        """, (session_id,))
    else:
        # Fallback: no cookie provided — return nothing (forces login)
        conn.close()
        return None

    row = cursor.fetchone()
    conn.close()

    if not row:
        return None

    return {
        "session_id": row["id"],
        "user_id": row["user_id"],
        "created_at": row["created_at"],
        "expires_at": row["expires_at"]
    }

def get_active_access_token(session_id: str = None):
    """Get the Zerodha access_token for a specific session cookie."""
    conn = get_connection()
    cursor = conn.cursor()

    if session_id:
        cursor.execute("""
            SELECT access_token
            FROM zerodha_sessions
            WHERE id = ? AND is_active = 1
            LIMIT 1
        """, (session_id,))
    else:
        conn.close()
        return None

    row = cursor.fetchone()
    conn.close()

    if not row:
        return None

    return row["access_token"]

def get_any_active_access_token() -> str | None:
    """Return the most recent active, non-expired token (for scheduler use)."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT access_token FROM zerodha_sessions
        WHERE is_active = 1 AND expires_at > datetime('now')
        ORDER BY created_at DESC LIMIT 1
    """)
    row = cursor.fetchone()
    conn.close()
    return row["access_token"] if row else None

def deactivate_session(session_id: str):
    """Deactivate a single session by its ID."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE zerodha_sessions
        SET is_active = 0
        WHERE id = ?
    """, (session_id,))
    conn.commit()
    conn.close()

def deactivate_all_sessions():
    """Set is_active = 0 for all active sessions (admin/cleanup)."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE zerodha_sessions
        SET is_active = 0
        WHERE is_active = 1
    """)
    conn.commit()
    conn.close()

def init_holdings_snapshot_table():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS holdings_snapshots (
            id TEXT PRIMARY KEY,
            snapshot_at TEXT NOT NULL,
            snapshot_type TEXT NOT NULL,   -- 'SOD' or 'EOD'
            tradingsymbol TEXT NOT NULL,
            exchange TEXT,
            quantity INTEGER,
            average_price REAL,
            last_price REAL,
            pnl REAL
        )
    """)

    conn.commit()
    conn.close()

import uuid
from datetime import datetime, time
import pytz

IST = pytz.timezone("Asia/Kolkata")

def save_holdings_snapshot(holdings: list):
    conn = get_connection()
    cursor = conn.cursor()

    now_ist = datetime.now(IST)
    today = now_ist.date().isoformat()

    snapshot_type = None

    # 🕣 Start of Day snapshot window
    if time(8, 30) <= now_ist.time() <= time(9, 15):
        snapshot_type = "SOD"

    # 🕟 End of Day snapshot window
    elif now_ist.time() >= time(16, 30):
        snapshot_type = "EOD"

    # ❌ Outside snapshot windows → do nothing
    if snapshot_type is None:
        conn.close()
        return

    # ❌ Prevent duplicate SOD/EOD snapshots for the same day
    cursor.execute("""
        SELECT 1 FROM holdings_snapshots
        WHERE DATE(snapshot_at) = ?
          AND snapshot_type = ?
        LIMIT 1
    """, (today, snapshot_type))

    if cursor.fetchone():
        conn.close()
        return

    snapshot_time = now_ist.isoformat()

    for h in holdings:
        cursor.execute("""
            INSERT INTO holdings_snapshots (
                id, snapshot_at, snapshot_type,
                tradingsymbol, exchange,
                quantity, average_price, last_price, pnl
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            str(uuid.uuid4()),
            snapshot_time,
            snapshot_type,
            h.get("tradingsymbol"),
            h.get("exchange"),
            h.get("quantity"),
            h.get("average_price"),
            h.get("last_price"),
            h.get("pnl")
        ))

    conn.commit()
    conn.close()

def create_instruments_table():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS instruments (
            symbol TEXT NOT NULL,
            exchange TEXT NOT NULL,
            company_name TEXT,
            sector TEXT,
            industry TEXT,
            isin TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (symbol, exchange)
        )
    """)

    conn.commit()
    conn.close()

def get_latest_snapshot_meta(tradingsymbol: str):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            MAX(snapshot_at) as last_snapshot_at,
            COUNT(*) as snapshot_count
        FROM holdings_snapshots
        WHERE tradingsymbol = ?
    """, (tradingsymbol,))

    row = cursor.fetchone()
    conn.close()

    if not row or not row[0]:
        return {
            "last_snapshot_at": None,
            "snapshot_count": 0
        }

    return {
        "last_snapshot_at": row[0],
        "snapshot_count": row[1]
    }

def upsert_instruments_from_holdings(holdings: list):
    """
    Populate instruments table using live Zerodha holdings.
    Inserts only if (symbol, exchange) does not already exist.
    """
    conn = get_connection()
    cursor = conn.cursor()

    query = """
        INSERT OR IGNORE INTO instruments (
            symbol,
            exchange,
            company_name,
            sector,
            industry,
            isin
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """

    for h in holdings:
        cursor.execute(
            query,
            (
                h.get("tradingsymbol"),
                h.get("exchange"),
                None,                  # company_name (later)
                None,                  # sector
                None,                  # industry
                h.get("isin")
            )
        )

    conn.commit()
    conn.close()

def enrich_instruments_with_sector():
    from backend.app.services.sector_map import get_sector_info

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT symbol, exchange FROM instruments")
    rows = cursor.fetchall()

    for row in rows:
        symbol = row["symbol"]
        info = get_sector_info(symbol)

        cursor.execute("""
            UPDATE instruments
            SET sector = ?, industry = ?
            WHERE symbol = ?
        """, (info["sector"], info["industry"], symbol))

    conn.commit()
    conn.close()

def get_instrument(symbol: str, exchange: str):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT * FROM instruments
        WHERE symbol = ? AND exchange = ?
        """,
        (symbol, exchange)
    )

    row = cursor.fetchone()
    conn.close()
    return row

def update_instrument_sector(symbol, exchange, sector, industry):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE instruments
        SET sector = ?, industry = ?
        WHERE symbol = ? AND exchange = ?
        """,
        (sector, industry, symbol, exchange)
    )

    conn.commit()
    conn.close()


# ─── Delivery Data Cache ───────────────────────────────────────────

def create_delivery_cache_table():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS delivery_cache (
            symbol TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            total_traded_qty INTEGER DEFAULT 0,
            delivered_qty INTEGER DEFAULT 0,
            not_delivered_qty INTEGER DEFAULT 0,
            delivery_pct REAL DEFAULT 0,
            price_up INTEGER DEFAULT 1,
            close_price REAL DEFAULT 0,
            open_price REAL DEFAULT 0,
            high_price REAL DEFAULT 0,
            low_price REAL DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (symbol, trade_date)
        )
    """)
    # Add OHLC columns if table already exists (migration for existing DBs)
    for col in ["close_price", "open_price", "high_price", "low_price"]:
        try:
            cursor.execute(f"ALTER TABLE delivery_cache ADD COLUMN {col} REAL DEFAULT 0")
        except Exception:
            pass  # Column already exists
    conn.commit()
    conn.close()


def _normalize_date_to_iso(date_str: str) -> str:
    """Convert DD-Mon-YYYY (e.g. '21-Nov-2025') to YYYY-MM-DD for SQLite."""
    from datetime import datetime as _dt
    try:
        return _dt.strptime(date_str, "%d-%b-%Y").strftime("%Y-%m-%d")
    except ValueError:
        return date_str  # Already ISO or unknown format


def save_delivery_cache(symbol: str, records: list):
    """Upsert delivery records for a symbol into cache. Dates stored as ISO."""
    if not records:
        return
    conn = get_connection()
    cursor = conn.cursor()
    for r in records:
        iso_date = _normalize_date_to_iso(r["date"])
        cursor.execute("""
            INSERT OR REPLACE INTO delivery_cache
                (symbol, trade_date, total_traded_qty, delivered_qty,
                 not_delivered_qty, delivery_pct, price_up,
                 close_price, open_price, high_price, low_price)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            symbol,
            iso_date,
            r.get("total_traded_qty", 0),
            r.get("delivered_qty", 0),
            r.get("not_delivered_qty", 0),
            r.get("delivery_pct", 0),
            1 if r.get("price_up", True) else 0,
            r.get("close_price", 0),
            r.get("open_price", 0),
            r.get("high_price", 0),
            r.get("low_price", 0)
        ))
    conn.commit()
    conn.close()


def get_delivery_cache(symbol: str, period_days: int = 365) -> list:
    """Read cached delivery data for a symbol within the given period."""
    from datetime import datetime as _dt
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT trade_date, total_traded_qty, delivered_qty,
               not_delivered_qty, delivery_pct, price_up,
               close_price, open_price, high_price, low_price
        FROM delivery_cache
        WHERE symbol = ?
          AND trade_date >= date('now', ?)
        ORDER BY trade_date ASC
    """, (symbol, f"-{period_days} days"))
    rows = cursor.fetchall()
    conn.close()

    results = []
    for row in rows:
        # Convert ISO date back to DD-Mon-YYYY for frontend display
        try:
            display_date = _dt.strptime(row["trade_date"], "%Y-%m-%d").strftime("%d-%b-%Y")
        except ValueError:
            display_date = row["trade_date"]
        results.append({
            "date": display_date,
            "total_traded_qty": row["total_traded_qty"],
            "delivered_qty": row["delivered_qty"],
            "not_delivered_qty": row["not_delivered_qty"],
            "delivery_pct": row["delivery_pct"],
            "price_up": bool(row["price_up"]),
            "close_price": row["close_price"] or 0,
            "open_price": row["open_price"] or 0,
            "high_price": row["high_price"] or 0,
            "low_price": row["low_price"] or 0
        })
    return results


# ─── Trades (Tradebook Import) ──────────────────────────────────────

def create_trades_table():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            isin TEXT,
            trade_date TEXT NOT NULL,
            exchange TEXT NOT NULL,
            segment TEXT,
            series TEXT,
            trade_type TEXT NOT NULL,
            auction TEXT,
            quantity REAL NOT NULL,
            price REAL NOT NULL,
            trade_id TEXT NOT NULL,
            order_id TEXT,
            order_execution_time TEXT,
            source_file TEXT,
            imported_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(trade_id, symbol, trade_date, exchange)
        )
    """)
    conn.commit()
    conn.close()
