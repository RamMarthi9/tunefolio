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
