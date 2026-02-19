"""
Daily Delivery Data Sync Script
================================
Run this daily from your local machine to sync delivery data from NSE
into the TuneFolio database. NSE blocks cloud server IPs, so this
must run from a residential/office IP.

Usage:
    python scripts/sync_delivery.py              # sync 1 year data
    python scripts/sync_delivery.py --period 3m  # sync 3 months

After syncing, the DB file (backend/data/tunefolio.db) needs to be
accessible by the Render deployment. Options:
    1. Push DB to repo (simple, works for personal use)
    2. Call the sync API endpoint instead (if running locally)

To use the API endpoint instead:
    curl -X POST "http://127.0.0.1:8000/portfolio/delivery-data/sync?period=1y"
"""
import sys
import os
import argparse
import time

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.app.services.db import (
    create_delivery_cache_table,
    get_connection
)
from backend.app.services.delivery import fetch_and_cache_delivery


def get_nse_symbols_from_db():
    """Get all NSE symbols from the instruments table."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT DISTINCT symbol FROM instruments
        WHERE exchange = 'NSE'
    """)
    rows = cursor.fetchall()
    conn.close()
    return [row["symbol"] for row in rows]


def main():
    parser = argparse.ArgumentParser(description="Sync delivery data from NSE")
    parser.add_argument("--period", default="1y", choices=["3m", "6m", "1y"],
                        help="Period to sync (default: 1y)")
    args = parser.parse_args()

    period_map = {"1y": 365, "6m": 180, "3m": 90}
    period_days = period_map[args.period]

    # Ensure table exists
    create_delivery_cache_table()

    # Get symbols
    symbols = get_nse_symbols_from_db()
    if not symbols:
        print("No NSE symbols found in instruments table.")
        print("Login to Zerodha first to populate holdings.")
        return

    print(f"Syncing delivery data for {len(symbols)} NSE symbols ({args.period})...")
    print("=" * 60)

    success = 0
    failed = 0

    for i, sym in enumerate(symbols, 1):
        try:
            data = fetch_and_cache_delivery(sym, period_days)
            count = len(data)
            status = f"{count} records" if count > 0 else "no data"
            print(f"  [{i}/{len(symbols)}] {sym:20s} -> {status}")
            if count > 0:
                success += 1
            else:
                failed += 1
        except Exception as e:
            print(f"  [{i}/{len(symbols)}] {sym:20s} -> ERROR: {e}")
            failed += 1

        # Small delay to avoid NSE rate limiting
        if i < len(symbols):
            time.sleep(1)

    print("=" * 60)
    print(f"Done. Success: {success}, Failed/Empty: {failed}, Total: {len(symbols)}")


if __name__ == "__main__":
    main()
