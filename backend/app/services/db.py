import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "tunefolio.db"

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

def get_active_zerodha_session():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, user_id, created_at, expires_at
        FROM zerodha_sessions
        WHERE is_active = 1
        ORDER BY created_at DESC
        LIMIT 1
    """)

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

def get_active_access_token():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT access_token
        FROM zerodha_sessions
        WHERE is_active = 1
        ORDER BY created_at DESC
        LIMIT 1
    """)

    row = cursor.fetchone()
    conn.close()

    if not row:
        return None

    return row["access_token"]

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
        SET sector = ?, industry = ?, updated_at = CURRENT_TIMESTAMP
        WHERE symbol = ? AND exchange = ?
        """,
        (sector, industry, symbol, exchange)
    )

    conn.commit()
    conn.close()


