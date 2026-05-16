import os
import math
import logging
from datetime import datetime, timedelta

import requests as _requests

from backend.app.services.db import save_delivery_cache, get_delivery_cache

logger = logging.getLogger(__name__)

# nselib/pandas only needed for live NSE fetching (not on Vercel — NSE blocks cloud IPs)
try:
    from nselib import capital_market
    import pandas as pd
except ImportError:
    capital_market = None
    pd = None


def _safe_float(val) -> float:
    """Convert a value to float, stripping commas from string representations."""
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return 0.0
    if isinstance(val, str):
        return float(val.replace(",", ""))
    return float(val)


def _safe_int(val) -> int:
    """Convert a value to int, stripping commas from string representations."""
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return 0
    if isinstance(val, str):
        return int(float(val.replace(",", "")))
    return int(val)


def _fetch_nse_chunk(symbol: str, start: datetime, end: datetime) -> list[dict]:
    """Fetch one chunk of delivery data from NSE (max ~365 days recommended)."""
    if capital_market is None:
        return []  # nselib not available (Vercel)

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


def _fetch_nse_direct(symbol: str, start: datetime, end: datetime) -> list[dict]:
    """
    Fetch delivery data directly from NSE API (no nselib dependency).
    Gets real delivered_qty and total_traded_qty so we can calculate:
    settled = total_traded - delivered.
    """
    session = _requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/125.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nseindia.com/",
    })

    try:
        # Step 1: Get session cookies from NSE homepage
        r = session.get("https://www.nseindia.com", timeout=10)
        if r.status_code != 200:
            logger.info("NSE direct: homepage returned %s", r.status_code)
            return []
    except Exception as e:
        logger.info("NSE direct: homepage failed: %s", e)
        return []

    from_date = start.strftime("%d-%m-%Y")
    to_date = end.strftime("%d-%m-%Y")
    url = (
        "https://www.nseindia.com/api/historical/securityArchives"
        f"?from={from_date}&to={to_date}&symbol={symbol}"
        "&dataType=priceVolumeDeliverable&series=EQ"
    )

    try:
        r = session.get(url, timeout=15)
        if r.status_code != 200:
            logger.info("NSE direct: API returned %s for %s", r.status_code, symbol)
            return []
        body = r.json()
    except Exception as e:
        logger.info("NSE direct: API failed for %s: %s", symbol, e)
        return []

    results = []
    for row in body.get("data", []):
        try:
            total_traded = _safe_int(row.get("CH_TOT_TRADED_QTY", 0))
            delivered = _safe_int(row.get("COP_DELIV_QTY", 0))
            not_delivered = max(total_traded - delivered, 0)
            delivery_pct = round(_safe_float(row.get("COP_DELIV_PERC", 0)), 2)

            close_price = _safe_float(row.get("CH_CLOSING_PRICE", 0))
            prev_close = _safe_float(row.get("CH_PREVIOUS_CLS_PRICE", 0))
            open_price = _safe_float(row.get("CH_OPENING_PRICE", 0))
            high_price = _safe_float(row.get("CH_TRADE_HIGH_PRICE", 0))
            low_price = _safe_float(row.get("CH_TRADE_LOW_PRICE", 0))

            price_up = close_price >= prev_close

            results.append({
                "date": str(row.get("CH_TIMESTAMP", "")),
                "total_traded_qty": total_traded,
                "delivered_qty": delivered,
                "not_delivered_qty": not_delivered,
                "delivery_pct": delivery_pct,
                "price_up": price_up,
                "close_price": close_price,
                "open_price": open_price,
                "high_price": high_price,
                "low_price": low_price,
            })
        except (ValueError, KeyError, TypeError):
            continue

    return results


def _fetch_nse_delivery(symbol: str, period_days: int = 365) -> list[dict]:
    """
    Fetch delivery data from NSE direct API in yearly chunks.
    Returns list with real delivered_qty for daily calculation.
    """
    end_date = datetime.now()
    start_date = end_date - timedelta(days=period_days)

    all_results = []
    chunk_start = start_date
    while chunk_start < end_date:
        chunk_end = min(chunk_start + timedelta(days=365), end_date)
        chunk_data = _fetch_nse_direct(symbol, chunk_start, chunk_end)
        all_results.extend(chunk_data)
        chunk_start = chunk_end + timedelta(days=1)

    if not all_results:
        return []

    # De-duplicate by date
    seen = set()
    unique = []
    for r in all_results:
        if r["date"] not in seen:
            seen.add(r["date"])
            unique.append(r)

    # NSE direct API returns dates as YYYY-MM-DD; convert to DD-Mon-YYYY
    for r in unique:
        try:
            r["date"] = datetime.strptime(r["date"], "%Y-%m-%d").strftime("%d-%b-%Y")
        except ValueError:
            pass

    try:
        unique.sort(key=lambda x: datetime.strptime(x["date"], "%d-%b-%Y"))
    except ValueError:
        pass

    return unique


def _fetch_yahoo_finance(symbol: str, period_days: int = 365) -> list[dict]:
    """Fetch OHLCV data from Yahoo Finance as fallback (works on Vercel)."""
    period_map = {90: "3mo", 180: "6mo", 365: "1y", 730: "2y", 1095: "3y"}
    yf_period = "1y"
    for days, period_str in sorted(period_map.items()):
        if period_days <= days:
            yf_period = period_str
            break
    else:
        yf_period = "5y"

    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}.NS"
        f"?range={yf_period}&interval=1d"
    )
    try:
        r = _requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
            verify=not os.getenv("VERCEL"),
        )
        if r.status_code != 200:
            return []
        data = r.json()
        result = data.get("chart", {}).get("result", [{}])[0]
        timestamps = result.get("timestamp", [])
        quotes = result.get("indicators", {}).get("quote", [{}])[0]
        if not timestamps:
            return []

        rows = []
        prev_close = None
        for i, ts in enumerate(timestamps):
            trade_date = datetime.utcfromtimestamp(ts).strftime("%d-%b-%Y")
            c = quotes.get("close", [None] * len(timestamps))[i]
            if c is None:
                continue
            o = quotes.get("open", [None] * len(timestamps))[i] or c
            h = quotes.get("high", [None] * len(timestamps))[i] or c
            lo = quotes.get("low", [None] * len(timestamps))[i] or c
            v = quotes.get("volume", [None] * len(timestamps))[i] or 0
            price_up = c >= prev_close if prev_close else True
            prev_close = c
            # Yahoo doesn't provide delivery breakdown, so use total
            # volume as "settled" (not_delivered) to keep bars visible.
            rows.append({
                "date": trade_date,
                "open_price": round(o, 2),
                "high_price": round(h, 2),
                "low_price": round(lo, 2),
                "close_price": round(c, 2),
                "total_traded_qty": v,
                "delivered_qty": 0,
                "not_delivered_qty": v,
                "delivery_pct": 0,
                "price_up": price_up,
            })
        return rows
    except Exception as e:
        logger.warning("Yahoo Finance fetch failed for %s: %s", symbol, e)
        return []


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
    Fallback chain to get real delivery data (delivered_qty + total_traded_qty):
      1. DB cache (has real NSE delivery data baked in seed DB)
      2. nselib (blocked on cloud IPs but works locally)
      3. NSE direct API (may work on Vercel with session handling)
      4. Yahoo Finance (OHLCV only — no delivery breakdown)
    """
    cached = get_delivery_cache(symbol, period_days)

    stale = False
    if cached:
        try:
            latest_str = cached[-1].get("date", "")
            latest_dt = datetime.strptime(latest_str, "%d-%b-%Y")
            stale = (datetime.now() - latest_dt).days > 3
        except (ValueError, IndexError):
            stale = True

    if not cached or stale:
        # Try nselib first (real delivery data, blocked on cloud)
        live_data = fetch_and_cache_delivery(symbol, period_days)
        if live_data:
            cached = get_delivery_cache(symbol, period_days)
        elif not cached or stale:
            # Try NSE direct API (real delivery data, may work on Vercel)
            nse_data = _fetch_nse_delivery(symbol, period_days)
            if nse_data:
                save_delivery_cache(symbol, nse_data)
                cached = get_delivery_cache(symbol, period_days)
            else:
                # Last resort: Yahoo Finance (volume only, no delivery breakdown)
                yf_data = _fetch_yahoo_finance(symbol, period_days)
                if yf_data:
                    save_delivery_cache(symbol, yf_data, overwrite=False)
                    cached = get_delivery_cache(symbol, period_days)

    return cached or []
