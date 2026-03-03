from datetime import datetime, timedelta
from nselib import capital_market
import pandas as pd

from backend.app.services.db import save_delivery_cache, get_delivery_cache


def _safe_float(val) -> float:
    """Convert a value to float, stripping commas from string representations."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return 0.0
    if isinstance(val, str):
        return float(val.replace(",", ""))
    return float(val)


def _safe_int(val) -> int:
    """Convert a value to int, stripping commas from string representations."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return 0
    if isinstance(val, str):
        return int(float(val.replace(",", "")))
    return int(val)


def _fetch_nse_chunk(symbol: str, start: datetime, end: datetime) -> list[dict]:
    """Fetch one chunk of delivery data from NSE (max ~365 days recommended)."""
    from_date = start.strftime("%d-%m-%Y")
    to_date = end.strftime("%d-%m-%Y")

    try:
        df = capital_market.price_volume_and_deliverable_position_data(
            symbol, from_date, to_date
        )
    except Exception:
        return []

    if df is None or (hasattr(df, 'empty') and df.empty):
        return []

    results = []
    for _, row in df.iterrows():
        try:
            total_traded = _safe_int(row.get("TotalTradedQuantity", 0))
            delivered = _safe_int(row.get("DeliverableQty", 0))
            not_delivered = max(total_traded - delivered, 0)
            delivery_pct = round(_safe_float(row.get("%DlyQttoTradedQty", 0)), 2)

            close_price = _safe_float(row.get("ClosePrice", 0))
            prev_close = _safe_float(row.get("PrevClose", 0))
            open_price = _safe_float(row.get("OpenPrice", 0))
            high_price = _safe_float(row.get("HighPrice", 0))
            low_price = _safe_float(row.get("LowPrice", 0))

            price_up = close_price >= prev_close

            results.append({
                "date": str(row["Date"]),
                "total_traded_qty": total_traded,
                "delivered_qty": delivered,
                "not_delivered_qty": not_delivered,
                "delivery_pct": delivery_pct,
                "price_up": price_up,
                "close_price": close_price,
                "open_price": open_price,
                "high_price": high_price,
                "low_price": low_price
            })
        except (ValueError, KeyError, TypeError):
            continue

    return results


def fetch_delivery_from_nse(symbol: str, period_days: int = 365) -> list[dict]:
    """
    Fetch delivery volume + price data from NSE via nselib.
    For periods > 365 days, fetches in yearly chunks to avoid NSE API limits.
    Returns list of dicts with price direction and OHLC prices.
    Returns empty list on any failure (NSE blocks cloud IPs, rate limits, etc.)
    """
    end_date = datetime.now()
    start_date = end_date - timedelta(days=period_days)

    # Split into yearly chunks for long periods
    all_results = []
    chunk_start = start_date
    while chunk_start < end_date:
        chunk_end = min(chunk_start + timedelta(days=365), end_date)
        chunk_data = _fetch_nse_chunk(symbol, chunk_start, chunk_end)
        all_results.extend(chunk_data)
        chunk_start = chunk_end + timedelta(days=1)

    if not all_results:
        return []

    # De-duplicate by date (chunks may overlap at boundaries)
    seen_dates = set()
    unique_results = []
    for r in all_results:
        if r["date"] not in seen_dates:
            seen_dates.add(r["date"])
            unique_results.append(r)

    try:
        unique_results.sort(key=lambda x: datetime.strptime(x["date"], "%d-%b-%Y"))
    except ValueError:
        pass

    return unique_results


def fetch_and_cache_delivery(symbol: str, period_days: int = 365) -> list[dict]:
    """
    Fetch from NSE, cache to DB, return results.
    Called from sync endpoints or local scripts.
    """
    data = fetch_delivery_from_nse(symbol, period_days)
    if data:
        save_delivery_cache(symbol, data)
    return data


def fetch_delivery_data(symbol: str, period_days: int = 365) -> list[dict]:
    """
    Primary function called by the API endpoint.
    1. Try DB cache first (always fast)
    2. If cache seems incomplete for the requested period, try live NSE fetch
    3. Return whatever we have (cache may have partial data for long periods)
    """
    # 1. Check cache — always return what we have
    cached = get_delivery_cache(symbol, period_days)

    # 2. If cache empty or has fewer data points than expected for this period,
    #    try live NSE fetch to supplement (works locally, may fail on Render)
    expected_min_days = period_days * 0.5  # ~50% of trading days in the range
    if not cached or (period_days > 365 and len(cached) < expected_min_days * 0.3):
        live_data = fetch_and_cache_delivery(symbol, period_days)
        if live_data:
            # Re-read from cache (now merged with new data)
            cached = get_delivery_cache(symbol, period_days)

    return cached or []
