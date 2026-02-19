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


def fetch_delivery_from_nse(symbol: str, period_days: int = 365) -> list[dict]:
    """
    Fetch delivery volume + price data from NSE via nselib.
    Returns list of dicts with price direction and OHLC prices.
    Returns empty list on any failure (NSE blocks cloud IPs, rate limits, etc.)
    """
    end_date = datetime.now()
    start_date = end_date - timedelta(days=period_days)

    from_date = start_date.strftime("%d-%m-%Y")
    to_date = end_date.strftime("%d-%m-%Y")

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

    if not results:
        return []

    try:
        results.sort(key=lambda x: datetime.strptime(x["date"], "%d-%b-%Y"))
    except ValueError:
        pass

    return results


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
    2. If cache empty, try live NSE fetch + cache it
    3. Return whatever we have
    """
    # 1. Check cache
    cached = get_delivery_cache(symbol, period_days)
    if cached:
        return cached

    # 2. Cache miss — try live fetch (works locally, fails on Render)
    live_data = fetch_and_cache_delivery(symbol, period_days)
    if live_data:
        return live_data

    return []
