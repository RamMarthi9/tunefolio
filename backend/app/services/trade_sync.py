import os
import logging
import requests
from datetime import datetime

from backend.app.services.db import get_any_active_access_token, get_connection

logger = logging.getLogger("tunefolio.trade_sync")

KITE_API_KEY = os.getenv("KITE_API_KEY")


def sync_trades_from_kite(access_token: str = None) -> dict:
    """
    Fetch today's trades from Kite API and insert into the trades table.
    Idempotent: uses INSERT OR IGNORE on UNIQUE(trade_id, symbol, trade_date, exchange).

    Args:
        access_token: Optional. If not provided, fetches the most recent active token.

    Returns:
        dict with status, inserted count, total fetched, and timestamp.
    """
    token = access_token or get_any_active_access_token()

    if not token:
        logger.info("Trade sync skipped: no active access token")
        return {"status": "skipped", "reason": "no_token", "timestamp": _now_iso()}

    api_key = KITE_API_KEY or os.getenv("KITE_API_KEY")
    if not api_key:
        logger.warning("Trade sync skipped: KITE_API_KEY not set")
        return {"status": "skipped", "reason": "no_api_key", "timestamp": _now_iso()}

    headers = {"Authorization": f"token {api_key}:{token}"}

    try:
        resp = requests.get("https://api.kite.trade/trades", headers=headers, timeout=15)
    except requests.RequestException as e:
        logger.error(f"Trade sync HTTP error: {e}")
        return {"status": "error", "reason": str(e), "timestamp": _now_iso()}

    if resp.status_code == 403:
        logger.info("Trade sync skipped: token expired (403)")
        return {"status": "skipped", "reason": "token_expired", "timestamp": _now_iso()}

    if resp.status_code != 200:
        logger.error(f"Trade sync failed: HTTP {resp.status_code}")
        return {"status": "error", "reason": f"http_{resp.status_code}", "timestamp": _now_iso()}

    trades = resp.json().get("data", [])

    if not trades:
        logger.info("Trade sync: 0 trades returned from Kite API")
        return {"status": "ok", "fetched": 0, "inserted": 0, "timestamp": _now_iso()}

    inserted = _insert_trades(trades)

    logger.info(f"Trade sync complete: {inserted} new trades inserted (fetched {len(trades)})")
    return {
        "status": "ok",
        "fetched": len(trades),
        "inserted": inserted,
        "timestamp": _now_iso(),
    }


def _insert_trades(trades: list) -> int:
    """Map Kite API trade objects to the trades table and INSERT OR IGNORE."""
    conn = get_connection()
    cursor = conn.cursor()
    inserted = 0

    for t in trades:
        fill_ts = t.get("fill_timestamp", "")
        trade_date = fill_ts[:10] if fill_ts else datetime.now().strftime("%Y-%m-%d")

        cursor.execute("""
            INSERT OR IGNORE INTO trades (
                symbol, isin, trade_date, exchange, segment, series,
                trade_type, auction, quantity, price,
                trade_id, order_id, order_execution_time, source_file
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            t.get("tradingsymbol"),
            None,                                           # isin (not in Kite trades response)
            trade_date,
            t.get("exchange"),
            t.get("product"),                               # CNC / MIS / NRML → segment
            None,                                           # series
            t.get("transaction_type", "").lower(),           # BUY→buy, SELL→sell
            None,                                           # auction
            t.get("quantity", 0),
            t.get("average_price", 0),
            str(t.get("trade_id", "")),
            str(t.get("order_id", "")),
            fill_ts,
            "kite_api_sync",
        ))

        if cursor.rowcount > 0:
            inserted += 1

    conn.commit()
    conn.close()
    return inserted


def _now_iso() -> str:
    return datetime.now().isoformat()
