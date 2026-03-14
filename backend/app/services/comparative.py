"""
Portfolio vs market indices comparative analysis.
Fetches index data from Yahoo Finance (with DB caching) and computes
normalised % returns for comparison with the user's portfolio.
"""

import logging
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import yfinance as yf

from backend.app.services.db import (
    get_connection,
    get_index_cache,
    get_index_cache_latest_date,
    save_index_cache_bulk,
)

logger = logging.getLogger(__name__)

# Yahoo Finance ticker map for Indian indices
INDEX_MAP = {
    "Nifty 50": "^NSEI",
    "Nifty 100": "^CNX100",
    "Bank Nifty": "^NSEBANK",
    "Smallcap": "^CNXSC",
    "Midcap 150": "NIFTYMIDCAP150.NS",
}


# ─── Index data fetching ────────────────────────────────────────────

def _fetch_and_cache_index(name: str, yf_symbol: str, period_days: int):
    """Fetch index data from yfinance, cache in DB, return list of {date, close}."""
    try:
        # Check cache freshness
        latest = get_index_cache_latest_date(yf_symbol)
        today_str = datetime.now().strftime("%Y-%m-%d")

        need_fetch = True
        if latest and latest >= (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d"):
            need_fetch = False  # cache is fresh enough

        if need_fetch:
            start_date = (datetime.now() - timedelta(days=period_days + 30)).strftime("%Y-%m-%d")
            if latest:
                # Only fetch from the day after last cached date
                start_date = (datetime.strptime(latest, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")

            df = yf.download(yf_symbol, start=start_date, end=today_str, progress=False)

            if not df.empty:
                rows = []
                for dt_idx, row in df.iterrows():
                    trade_date = dt_idx.strftime("%Y-%m-%d")
                    # Handle multi-index columns from yfinance
                    close_col = ("Close", yf_symbol)
                    if close_col in df.columns:
                        close_val = float(row[close_col])
                    else:
                        close_val = float(row["Close"])
                    if close_val > 0:
                        rows.append((trade_date, close_val))
                if rows:
                    save_index_cache_bulk(yf_symbol, rows)
                    logger.info(f"Cached {len(rows)} rows for {name} ({yf_symbol})")

        # Return from cache
        return name, get_index_cache(yf_symbol, period_days)

    except Exception as e:
        logger.warning(f"Failed to fetch index {name} ({yf_symbol}): {e}")
        # Try returning whatever is in cache
        return name, get_index_cache(yf_symbol, period_days)


# ─── Portfolio value series ─────────────────────────────────────────

def _compute_portfolio_series(period_days: int, session_id: str = None):
    """
    Compute daily portfolio value from delivery_cache close prices.
    Uses current holdings quantities × historical close prices.
    """
    # Get current holdings quantities
    holdings_qty = {}
    try:
        from backend.app.services.zerodha_holdings import fetch_zerodha_holdings
        holdings = fetch_zerodha_holdings(session_id)
        for h in holdings:
            sym = h["tradingsymbol"]
            qty = h.get("quantity", 0)
            if qty > 0:
                holdings_qty[sym] = qty
    except Exception:
        # Fallback to latest snapshot
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT tradingsymbol, quantity FROM holdings_snapshots
            WHERE snapshot_at = (SELECT MAX(snapshot_at) FROM holdings_snapshots)
        """)
        for r in cursor.fetchall():
            if r["quantity"] > 0:
                holdings_qty[r["tradingsymbol"]] = r["quantity"]
        conn.close()

    if not holdings_qty:
        return []

    # Get close prices from delivery_cache for all holdings
    conn = get_connection()
    cursor = conn.cursor()

    # Build date → total_value map
    date_values = {}
    symbols_with_data = 0

    for sym, qty in holdings_qty.items():
        cursor.execute("""
            SELECT trade_date, close_price FROM delivery_cache
            WHERE symbol = ? AND trade_date >= date('now', ?)
            ORDER BY trade_date ASC
        """, (sym, f"-{period_days} days"))
        rows = cursor.fetchall()
        if not rows:
            continue
        symbols_with_data += 1
        for r in rows:
            d = r["trade_date"]
            if d not in date_values:
                date_values[d] = 0
            date_values[d] += r["close_price"] * qty

    conn.close()

    if not date_values:
        return []

    # Sort by date and return
    sorted_dates = sorted(date_values.keys())
    return [{"date": d, "close": date_values[d]} for d in sorted_dates]


# ─── Normalisation ──────────────────────────────────────────────────

def _normalize_to_pct(series):
    """Convert [{date, close}, ...] to [{date, pct}, ...] where pct = % return from start."""
    if not series:
        return []
    base = None
    for s in series:
        if s["close"] and s["close"] > 0:
            base = s["close"]
            break
    if not base:
        return []
    return [{"date": s["date"], "pct": round(((s["close"] / base) - 1) * 100, 2)} for s in series]


# ─── Main orchestrator ──────────────────────────────────────────────

def get_comparative_data(period_days: int, session_id: str = None):
    """
    Returns {dates: [...], series: {"Portfolio": [...], "Nifty 50": [...], ...}}
    All series are normalised to % returns from the start of the period.
    """
    results = {}

    # Fetch indices in parallel
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {}
        for name, yf_sym in INDEX_MAP.items():
            fut = executor.submit(_fetch_and_cache_index, name, yf_sym, period_days)
            futures[fut] = name

        for fut in as_completed(futures):
            try:
                name, data = fut.result()
                if data:
                    results[name] = _normalize_to_pct(data)
            except Exception as e:
                logger.warning(f"Index future failed: {e}")

    # Portfolio series
    portfolio_raw = _compute_portfolio_series(period_days, session_id)
    if portfolio_raw:
        results["Portfolio"] = _normalize_to_pct(portfolio_raw)

    if not results:
        return {"dates": [], "series": {}}

    # Find common date range (union of all dates)
    all_dates = set()
    for series in results.values():
        for pt in series:
            all_dates.add(pt["date"])
    sorted_dates = sorted(all_dates)

    # Build aligned series (forward-fill missing values)
    aligned = {}
    for name, series in results.items():
        date_map = {pt["date"]: pt["pct"] for pt in series}
        aligned_values = []
        last_val = 0
        for d in sorted_dates:
            if d in date_map:
                last_val = date_map[d]
            aligned_values.append(last_val)
        aligned[name] = aligned_values

    return {
        "dates": sorted_dates,
        "series": aligned,
    }
