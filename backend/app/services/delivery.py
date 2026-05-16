import os
import math
import logging
import tempfile
from datetime import datetime, timedelta

import requests as _requests

from backend.app.services.db import save_delivery_cache, get_delivery_cache

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional NSE library imports — each may be unavailable on Vercel
# ---------------------------------------------------------------------------

# 1. nselib — historical delivery data (date-range queries)
try:
    from nselib import capital_market
    import pandas as pd
except ImportError:
    capital_market = None
    pd = None

# 2. nsepython — current-day delivery data
try:
    from nsepython import nse_eq as _nse_eq
except ImportError:
    _nse_eq = None

# 3. nse (BennyThadikaran NseIndiaApi) — delivery bhavcopy CSV downloads
try:
    from nse import NSE as _NseBhavcopy
except ImportError:
    _NseBhavcopy = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _build_row(date_str, total_traded, delivered, close_price, prev_close,
               open_price, high_price, low_price, delivery_pct=None) -> dict:
    """Create a standardised delivery-data row dict."""
    not_delivered = max(total_traded - delivered, 0)
    if delivery_pct is None:
        delivery_pct = round((delivered / total_traded * 100) if total_traded else 0, 2)
    return {
        "date": date_str,
        "total_traded_qty": total_traded,
        "delivered_qty": delivered,
        "not_delivered_qty": not_delivered,
        "delivery_pct": round(delivery_pct, 2),
        "price_up": close_price >= prev_close if prev_close else True,
        "close_price": close_price,
        "open_price": open_price,
        "high_price": high_price,
        "low_price": low_price,
    }


def _dedup_and_sort(results: list[dict]) -> list[dict]:
    """De-duplicate by date and sort chronologically."""
    seen = set()
    unique = []
    for r in results:
        if r["date"] not in seen:
            seen.add(r["date"])
            unique.append(r)
    try:
        unique.sort(key=lambda x: datetime.strptime(x["date"], "%d-%b-%Y"))
    except ValueError:
        pass
    return unique


# ===================================================================
# Source 1: nselib  (historical date-range, real delivery data)
# ===================================================================

def _fetch_nselib_chunk(symbol: str, start: datetime, end: datetime) -> list[dict]:
    if capital_market is None:
        return []
    try:
        df = capital_market.price_volume_and_deliverable_position_data(
            symbol, start.strftime("%d-%m-%Y"), end.strftime("%d-%m-%Y")
        )
    except Exception:
        return []
    if df is None or (hasattr(df, "empty") and df.empty):
        return []

    results = []
    for _, row in df.iterrows():
        try:
            results.append(_build_row(
                date_str=str(row["Date"]),
                total_traded=_safe_int(row.get("TotalTradedQuantity", 0)),
                delivered=_safe_int(row.get("DeliverableQty", 0)),
                close_price=_safe_float(row.get("ClosePrice", 0)),
                prev_close=_safe_float(row.get("PrevClose", 0)),
                open_price=_safe_float(row.get("OpenPrice", 0)),
                high_price=_safe_float(row.get("HighPrice", 0)),
                low_price=_safe_float(row.get("LowPrice", 0)),
                delivery_pct=_safe_float(row.get("%DlyQttoTradedQty", 0)),
            ))
        except (ValueError, KeyError, TypeError):
            continue
    return results


def _fetch_nselib(symbol: str, period_days: int = 365) -> list[dict]:
    """Fetch delivery data via nselib in yearly chunks."""
    end_date = datetime.now()
    start_date = end_date - timedelta(days=period_days)
    all_results = []
    chunk_start = start_date
    while chunk_start < end_date:
        chunk_end = min(chunk_start + timedelta(days=365), end_date)
        all_results.extend(_fetch_nselib_chunk(symbol, chunk_start, chunk_end))
        chunk_start = chunk_end + timedelta(days=1)
    return _dedup_and_sort(all_results) if all_results else []


# ===================================================================
# Source 2: nsepython  (current-day delivery data)
# ===================================================================

def _fetch_nsepython(symbol: str) -> list[dict]:
    """Fetch today's delivery data using nsepython nse_eq()."""
    if _nse_eq is None:
        return []
    try:
        data = _nse_eq(symbol)
        dp = data.get("securityWiseDP", {})
        total_traded = _safe_int(dp.get("quantityTraded", 0))
        delivered = _safe_int(dp.get("deliveryQuantity", 0))
        if total_traded == 0:
            return []

        pi = data.get("priceInfo", {})
        intra = pi.get("intraDayHighLow", {})
        trade_date = datetime.now().strftime("%d-%b-%Y")

        return [_build_row(
            date_str=trade_date,
            total_traded=total_traded,
            delivered=delivered,
            close_price=_safe_float(pi.get("close", 0) or pi.get("lastPrice", 0)),
            prev_close=_safe_float(pi.get("previousClose", 0)),
            open_price=_safe_float(pi.get("open", 0)),
            high_price=_safe_float(intra.get("max", 0)),
            low_price=_safe_float(intra.get("min", 0)),
            delivery_pct=_safe_float(dp.get("deliveryToTradedQuantity", 0)),
        )]
    except Exception as e:
        logger.info("nsepython failed for %s: %s", symbol, e)
        return []


# ===================================================================
# Source 3: nse / Bhavcopy  (delivery bhavcopy CSV per date)
# ===================================================================

_BHAVCOPY_DIR = os.path.join(tempfile.gettempdir(), "nse_bhavcopy")


def _fetch_bhavcopy(symbol: str, period_days: int = 30) -> list[dict]:
    """
    Download delivery bhavcopy CSVs from NSE for recent dates.
    Capped at ~30 days to avoid timeout (one HTTP download per date).
    """
    if _NseBhavcopy is None:
        return []

    import csv

    os.makedirs(_BHAVCOPY_DIR, exist_ok=True)
    results = []
    end_date = datetime.now()
    start_date = end_date - timedelta(days=min(period_days, 30))
    current = start_date

    try:
        with _NseBhavcopy(_BHAVCOPY_DIR) as nse_ctx:
            while current <= end_date:
                # Skip weekends
                if current.weekday() >= 5:
                    current += timedelta(days=1)
                    continue
                try:
                    fpath = nse_ctx.deliveryBhavcopy(date=current)
                    if fpath and os.path.isfile(fpath):
                        with open(fpath, newline="") as f:
                            reader = csv.DictReader(f)
                            for row in reader:
                                sym = (row.get("SYMBOL") or row.get("SYM") or "").strip()
                                series = (row.get("SERIES") or row.get("SRS") or "").strip()
                                if sym == symbol and series == "EQ":
                                    total_traded = _safe_int(
                                        row.get("TTL_TRD_QNTY", 0) or row.get("TOTAL_TRADED_QUANTITY", 0)
                                    )
                                    delivered = _safe_int(
                                        row.get("DELIV_QTY", 0) or row.get("DELIVERY_QTY", 0)
                                    )
                                    if total_traded == 0:
                                        break
                                    close_p = _safe_float(row.get("CLOSE_PRICE", 0) or row.get("CLS_PRICE", 0))
                                    prev_p = _safe_float(row.get("PREV_CLOSE", 0) or row.get("PREV_CL_PR", 0))
                                    open_p = _safe_float(row.get("OPEN_PRICE", 0) or row.get("OPEN_PRICE", 0))
                                    high_p = _safe_float(row.get("HIGH_PRICE", 0) or row.get("HI_PRICE", 0))
                                    low_p = _safe_float(row.get("LOW_PRICE", 0) or row.get("LO_PRICE", 0))
                                    results.append(_build_row(
                                        date_str=current.strftime("%d-%b-%Y"),
                                        total_traded=total_traded,
                                        delivered=delivered,
                                        close_price=close_p,
                                        prev_close=prev_p,
                                        open_price=open_p,
                                        high_price=high_p,
                                        low_price=low_p,
                                    ))
                                    break  # found our symbol
                except Exception:
                    pass  # holiday / no file — skip
                current += timedelta(days=1)
    except Exception as e:
        logger.info("Bhavcopy fetch failed for %s: %s", symbol, e)

    return _dedup_and_sort(results) if results else []


# ===================================================================
# Source 4: NSE direct API  (session-based, no library dependency)
# ===================================================================

def _fetch_nse_direct(symbol: str, start: datetime, end: datetime) -> list[dict]:
    """Fetch delivery data directly from NSE API with session handling."""
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
        r = session.get("https://www.nseindia.com", timeout=10)
        if r.status_code != 200:
            return []
    except Exception:
        return []

    url = (
        "https://www.nseindia.com/api/historical/securityArchives"
        f"?from={start.strftime('%d-%m-%Y')}&to={end.strftime('%d-%m-%Y')}"
        f"&symbol={symbol}&dataType=priceVolumeDeliverable&series=EQ"
    )
    try:
        r = session.get(url, timeout=15)
        if r.status_code != 200:
            return []
        body = r.json()
    except Exception:
        return []

    results = []
    for row in body.get("data", []):
        try:
            ts = str(row.get("CH_TIMESTAMP", ""))
            try:
                ts = datetime.strptime(ts, "%Y-%m-%d").strftime("%d-%b-%Y")
            except ValueError:
                pass
            results.append(_build_row(
                date_str=ts,
                total_traded=_safe_int(row.get("CH_TOT_TRADED_QTY", 0)),
                delivered=_safe_int(row.get("COP_DELIV_QTY", 0)),
                close_price=_safe_float(row.get("CH_CLOSING_PRICE", 0)),
                prev_close=_safe_float(row.get("CH_PREVIOUS_CLS_PRICE", 0)),
                open_price=_safe_float(row.get("CH_OPENING_PRICE", 0)),
                high_price=_safe_float(row.get("CH_TRADE_HIGH_PRICE", 0)),
                low_price=_safe_float(row.get("CH_TRADE_LOW_PRICE", 0)),
                delivery_pct=_safe_float(row.get("COP_DELIV_PERC", 0)),
            ))
        except (ValueError, KeyError, TypeError):
            continue
    return results


def _fetch_nse_direct_chunked(symbol: str, period_days: int = 365) -> list[dict]:
    """Fetch delivery data from NSE direct API in yearly chunks."""
    end_date = datetime.now()
    start_date = end_date - timedelta(days=period_days)
    all_results = []
    chunk_start = start_date
    while chunk_start < end_date:
        chunk_end = min(chunk_start + timedelta(days=365), end_date)
        all_results.extend(_fetch_nse_direct(symbol, chunk_start, chunk_end))
        chunk_start = chunk_end + timedelta(days=1)
    return _dedup_and_sort(all_results) if all_results else []


# ===================================================================
# Source 5: Yahoo Finance  (OHLCV only — NO delivery breakdown)
# ===================================================================

def _fetch_yahoo_finance(symbol: str, period_days: int = 365) -> list[dict]:
    """Fetch OHLCV from Yahoo Finance. Has total volume but NO delivery data."""
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
            url, headers={"User-Agent": "Mozilla/5.0"},
            timeout=10, verify=not os.getenv("VERCEL"),
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
            rows.append(_build_row(
                date_str=trade_date,
                total_traded=v,
                delivered=0,        # Yahoo has no delivery data
                close_price=round(c, 2),
                prev_close=prev_close or c,
                open_price=round(o, 2),
                high_price=round(h, 2),
                low_price=round(lo, 2),
                delivery_pct=0,     # Yahoo has no delivery data
            ))
            prev_close = c
        return rows
    except Exception as e:
        logger.warning("Yahoo Finance fetch failed for %s: %s", symbol, e)
        return []


# ===================================================================
# Main entry point — fallback chain
# ===================================================================

def fetch_and_cache_delivery(symbol: str, period_days: int = 365) -> list[dict]:
    """Fetch from nselib, cache to DB, return results."""
    data = _fetch_nselib(symbol, period_days)
    if data:
        save_delivery_cache(symbol, data)
    return data


def fetch_delivery_data(symbol: str, period_days: int = 365) -> list[dict]:
    """
    Primary function called by the API endpoint.

    Fallback chain — every source returns REAL data, no estimation:
      1. DB cache (seed DB has real NSE delivery data)
      2. nselib          — historical delivery data (date range)
      3. NSE direct API  — historical delivery data (session-based)
      4. Bhavcopy (nse)  — delivery CSV per date (recent ~30 days)
      5. nsepython       — current-day delivery data only
      6. Yahoo Finance   — OHLCV only (delivered_qty = 0, no breakdown)

    Formula used everywhere:
      Settled Qty = Total Traded Qty − Delivered Qty
      Percentages calculated from actual daily quantities.
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
        # --- Try sources with full delivery breakdown first ---

        # 1. nselib (full date range, real delivery data)
        live = _fetch_nselib(symbol, period_days)
        if live:
            save_delivery_cache(symbol, live)
            cached = get_delivery_cache(symbol, period_days)
        else:
            # 2. NSE direct API (full date range, real delivery data)
            live = _fetch_nse_direct_chunked(symbol, period_days)
            if live:
                save_delivery_cache(symbol, live)
                cached = get_delivery_cache(symbol, period_days)
            else:
                # 3. Bhavcopy — recent ~30 days with real delivery data
                bv = _fetch_bhavcopy(symbol, min(period_days, 30))
                if bv:
                    # INSERT OR IGNORE: preserve existing NSE rows
                    save_delivery_cache(symbol, bv, overwrite=False)
                    cached = get_delivery_cache(symbol, period_days)

        # 4. nsepython — always try for today's delivery data
        today_data = _fetch_nsepython(symbol)
        if today_data:
            save_delivery_cache(symbol, today_data, overwrite=False)
            cached = get_delivery_cache(symbol, period_days)

        # 5. Yahoo Finance — last resort, OHLCV only (no delivery breakdown)
        if not cached or stale:
            yf = _fetch_yahoo_finance(symbol, period_days)
            if yf:
                # INSERT OR IGNORE: never overwrite real delivery data
                save_delivery_cache(symbol, yf, overwrite=False)
                cached = get_delivery_cache(symbol, period_days)

    return cached or []
