from datetime import datetime, timedelta
from nselib import capital_market
import pandas as pd

from backend.app.services.db import save_delivery_cache, get_delivery_cache


def fetch_delivery_from_nse(symbol: str, period_days: int = 365) -> list[dict]:
    """
    Fetch delivery volume data from NSE via nselib.
    Returns list of dicts with price direction.
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
            total_traded = int(row["TotalTradedQuantity"]) if pd.notna(row.get("TotalTradedQuantity")) else 0
            delivered = int(row["DeliverableQty"]) if pd.notna(row.get("DeliverableQty")) else 0
            not_delivered = max(total_traded - delivered, 0)
            delivery_pct = round(float(row["%DlyQttoTradedQty"]), 2) if pd.notna(row.get("%DlyQttoTradedQty")) else 0

            close_price = float(row["ClosePrice"]) if pd.notna(row.get("ClosePrice")) else 0
            prev_close = float(row["PrevClose"]) if pd.notna(row.get("PrevClose")) else 0
            price_up = close_price >= prev_close

            results.append({
                "date": str(row["Date"]),
                "total_traded_qty": total_traded,
                "delivered_qty": delivered,
                "not_delivered_qty": not_delivered,
                "delivery_pct": delivery_pct,
                "price_up": price_up
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
