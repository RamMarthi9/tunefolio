"""
Trades service: CSV tradebook import, FIFO realised P&L engine, FY helpers.
"""

import csv
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from backend.app.services.db import get_connection, DB_PATH

DATA_DIR = DB_PATH.parent  # backend/data/


# ─── Date Normalization ──────────────────────────────────────────────

def normalize_trade_date(date_str: str) -> str:
    """Convert trade_date from CSV to YYYY-MM-DD ISO format.

    Handles two formats found in Zerodha tradebook exports:
      - YYYY-MM-DD  (files 0, 2, 5)
      - M/D/YYYY    (files 1, 3, 4)
    """
    date_str = date_str.strip()
    # Already ISO?
    if len(date_str) >= 8 and date_str[4] == "-":
        return date_str[:10]
    # M/D/YYYY
    try:
        return datetime.strptime(date_str, "%m/%d/%Y").strftime("%Y-%m-%d")
    except ValueError:
        pass
    # D/M/YYYY fallback
    try:
        return datetime.strptime(date_str, "%d/%m/%Y").strftime("%Y-%m-%d")
    except ValueError:
        return date_str  # Unknown — return as-is


# ─── CSV Import ──────────────────────────────────────────────────────

def import_tradebooks() -> dict:
    """
    Parse all tradebook CSVs from backend/data/ and INSERT OR IGNORE
    into the trades table.  Idempotent — re-running inserts 0 new rows.

    Returns: {filename: rows_inserted, ...}
    """
    csv_files = sorted(DATA_DIR.glob("tradebook-QX1480-EQ*.csv"))
    conn = get_connection()
    cursor = conn.cursor()
    summary = {}

    for csv_path in csv_files:
        filename = csv_path.name
        inserted = 0
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                iso_date = normalize_trade_date(row["trade_date"])
                try:
                    cursor.execute("""
                        INSERT OR IGNORE INTO trades (
                            symbol, isin, trade_date, exchange, segment,
                            series, trade_type, auction, quantity, price,
                            trade_id, order_id, order_execution_time, source_file
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        row["symbol"].strip(),
                        row["isin"].strip(),
                        iso_date,
                        row["exchange"].strip(),
                        row.get("segment", "").strip(),
                        row.get("series", "").strip(),
                        row["trade_type"].strip().lower(),
                        row.get("auction", "").strip().lower(),
                        float(row["quantity"]),
                        float(row["price"]),
                        str(row["trade_id"]).strip(),
                        str(row.get("order_id", "")).strip(),
                        row.get("order_execution_time", "").strip(),
                        filename,
                    ))
                    if cursor.rowcount > 0:
                        inserted += 1
                except Exception as e:
                    print(f"Skipping row in {filename}: {e}")
                    continue
        summary[filename] = inserted

    conn.commit()
    conn.close()
    return summary


# ─── Financial Year Helpers ──────────────────────────────────────────

def get_fy_bounds(fy_label: str = None) -> tuple:
    """
    Given FY label like 'FY2025-26', return (start_date, end_date) as
    ISO strings.  If None, returns current FY bounds.

    Indian FY: April 1 to March 31.
    FY2025-26 = 2025-04-01 to 2026-03-31
    """
    if fy_label:
        start_year = int(fy_label[2:6])
    else:
        today = datetime.now()
        start_year = today.year if today.month >= 4 else today.year - 1

    return (f"{start_year}-04-01", f"{start_year + 1}-03-31")


def get_available_fys() -> list:
    """Return sorted list of FY labels that have sell trades."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT DISTINCT trade_date FROM trades
        WHERE trade_type = 'sell'
        ORDER BY trade_date ASC
    """)
    rows = cursor.fetchall()
    conn.close()

    fys = set()
    for row in rows:
        d = datetime.strptime(row["trade_date"], "%Y-%m-%d")
        fy_start = d.year if d.month >= 4 else d.year - 1
        fys.add(f"FY{fy_start}-{str(fy_start + 1)[-2:]}")

    return sorted(fys)


# ─── FIFO Realised P&L Engine ────────────────────────────────────────

def compute_realised_pnl(fy_start: str = None, fy_end: str = None) -> dict:
    """
    Compute realised P&L using FIFO method across all symbols.

    Even when filtering by FY, ALL historical buys are loaded because
    a stock bought in FY2020-21 may be sold in FY2025-26.  The FY
    window filters *sells* only.

    Returns:
        {
            "total_realised_pnl": float,
            "by_symbol": {"SYM": {"realised_pnl": float, "qty_sold": float}},
            "total_symbols_sold": int,
            "total_sells": int
        }
    """
    conn = get_connection()
    cursor = conn.cursor()

    # Load ALL trades, ordered chronologically per symbol
    cursor.execute("""
        SELECT symbol, isin, trade_date, trade_type, quantity, price,
               order_execution_time, exchange, trade_id
        FROM trades
        ORDER BY symbol, trade_date ASC, order_execution_time ASC
    """)
    rows = cursor.fetchall()
    conn.close()

    # Group by symbol (pool across exchanges — Indian tax treatment)
    symbol_trades = defaultdict(list)
    for row in rows:
        symbol_trades[row["symbol"]].append(dict(row))

    total_rpnl = 0.0
    total_sells = 0
    by_symbol = {}

    for symbol, trades in symbol_trades.items():
        buy_queue = []  # FIFO: [{qty_remaining, price, date}, ...]
        symbol_rpnl = 0.0
        qty_sold = 0.0

        for t in trades:
            qty = float(t["quantity"])
            price = float(t["price"])

            if t["trade_type"] == "buy":
                buy_queue.append({
                    "qty_remaining": qty,
                    "price": price,
                    "date": t["trade_date"],
                })

            elif t["trade_type"] == "sell":
                # Is this sell within the requested FY window?
                sell_in_window = True
                if fy_start and t["trade_date"] < fy_start:
                    sell_in_window = False
                if fy_end and t["trade_date"] > fy_end:
                    sell_in_window = False

                sell_qty_remaining = qty
                sell_rpnl = 0.0

                while sell_qty_remaining > 0.0001 and buy_queue:
                    oldest = buy_queue[0]
                    match_qty = min(sell_qty_remaining, oldest["qty_remaining"])
                    pnl = (price - oldest["price"]) * match_qty

                    if sell_in_window:
                        sell_rpnl += pnl

                    oldest["qty_remaining"] -= match_qty
                    sell_qty_remaining -= match_qty

                    if oldest["qty_remaining"] <= 0.0001:
                        buy_queue.pop(0)

                if sell_in_window:
                    symbol_rpnl += sell_rpnl
                    qty_sold += qty
                    total_sells += 1

        if qty_sold > 0:
            by_symbol[symbol] = {
                "realised_pnl": round(symbol_rpnl, 2),
                "qty_sold": round(qty_sold, 2),
            }
            total_rpnl += symbol_rpnl

    return {
        "total_realised_pnl": round(total_rpnl, 2),
        "by_symbol": by_symbol,
        "total_symbols_sold": len(by_symbol),
        "total_sells": total_sells,
    }


# ─── Historical Holdings (Fully Exited Positions) ─────────────────────

def compute_historical_holdings(current_symbols: list = None) -> list:
    """
    Find all symbols that were fully exited (total buy qty == total sell qty)
    and are NOT in the current holdings list.

    Returns list of dicts with avg buy/sell prices, total P&L, dates.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT symbol, trade_date, trade_type, quantity, price, exchange, isin
        FROM trades
        ORDER BY symbol, trade_date ASC, order_execution_time ASC
    """)
    rows = cursor.fetchall()
    conn.close()

    current_set = set(current_symbols or [])

    # Group by symbol
    symbol_trades = defaultdict(list)
    for row in rows:
        symbol_trades[row["symbol"]].append(dict(row))

    results = []
    for symbol, trades in symbol_trades.items():
        if symbol in current_set:
            continue

        total_buy_qty = 0.0
        total_sell_qty = 0.0
        total_buy_value = 0.0
        total_sell_value = 0.0
        first_buy_date = None
        last_sell_date = None
        exchange = trades[0]["exchange"] if trades else "NSE"
        isin = None

        for t in trades:
            qty = float(t["quantity"])
            price = float(t["price"])

            if t["trade_type"] == "buy":
                total_buy_qty += qty
                total_buy_value += qty * price
                if not first_buy_date:
                    first_buy_date = t["trade_date"]
                if not isin and t.get("isin"):
                    isin = t["isin"]
            elif t["trade_type"] == "sell":
                total_sell_qty += qty
                total_sell_value += qty * price
                last_sell_date = t["trade_date"]

        # Only include fully exited positions
        if abs(total_buy_qty - total_sell_qty) > 0.01:
            continue
        if total_buy_qty == 0:
            continue

        avg_buy = round(total_buy_value / total_buy_qty, 2)
        avg_sell = round(total_sell_value / total_sell_qty, 2) if total_sell_qty else 0
        total_pnl = round(total_sell_value - total_buy_value, 2)

        results.append({
            "symbol": symbol,
            "exchange": exchange,
            "isin": isin,
            "avg_buy_price": avg_buy,
            "avg_sell_price": avg_sell,
            "total_qty_traded": round(total_buy_qty, 2),
            "total_invested": round(total_buy_value, 2),
            "total_proceeds": round(total_sell_value, 2),
            "total_pnl": total_pnl,
            "first_buy_date": first_buy_date,
            "last_sell_date": last_sell_date,
        })

    return results
