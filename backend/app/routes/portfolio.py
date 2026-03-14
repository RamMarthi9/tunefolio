from fastapi import APIRouter, HTTPException, Request

from backend.app.services.zerodha_holdings import fetch_zerodha_holdings, fetch_zerodha_margins
from backend.app.services.db import (
    get_latest_snapshot_meta,
    upsert_instruments_from_holdings,
    get_instrument,
    get_active_access_token,
)
from backend.app.services.db import get_connection
from backend.app.services.trade_sync import sync_trades_from_kite

# These use heavy deps (yfinance, apscheduler) — import conditionally
try:
    from backend.app.services.instruments import enrich_instrument_if_missing
except ImportError:
    enrich_instrument_if_missing = None

try:
    from backend.app.services.scheduler import get_scheduler_status
except ImportError:
    def get_scheduler_status():
        return {"running": False, "jobs": []}

router = APIRouter(prefix="/portfolio", tags=["Portfolio"])


# ─── Daily P&L ─────────────────────────────────────────────────────

@router.get("/daily-pnl")
def daily_pnl(request: Request):
    """
    Time-aware daily P&L:

    After 5 PM IST   → "Today's P&L"     (Kite: last_price − close_price)
    Before 9 AM IST  → "Yesterday's P&L"  (Kite: same data, market hasn't opened)
    9 AM – 5 PM IST  → "Yesterday's P&L"  (delivery cache: last 2 close prices)

    The Kite API `close_price` = previous trading day's close,
    `last_price` = current/last traded price. Between market close
    (3:30 PM) and next market open (9:15 AM), these don't change,
    so both post-market and pre-market use the same Kite data.
    """
    import pytz
    from datetime import datetime as _dt, timedelta

    IST = pytz.timezone("Asia/Kolkata")
    now_ist = _dt.now(IST)

    # Use Kite API when market is closed (after 5 PM or before 9 AM)
    # Use delivery cache during market hours (9 AM – 5 PM)
    use_kite = now_ist.hour >= 17 or now_ist.hour < 9

    from backend.app.services.trades import compute_realised_pnl

    if use_kite:
        # ── Kite API path (post-market or pre-market) ──
        label = "Today's P&L" if now_ist.hour >= 17 else "Yesterday's P&L"

        session_id = request.cookies.get("tf_session")
        unrealised_daily = 0.0
        stock_count = 0
        per_stock = []
        try:
            holdings = fetch_zerodha_holdings(session_id)
            for h in holdings:
                prev_close = h.get("close_price", 0) or 0
                last_price = h.get("last_price", 0) or 0
                qty = h.get("quantity", 0) or 0
                change = (last_price - prev_close) * qty
                unrealised_daily += change
                stock_count += 1
                if abs(change) > 0.01:
                    per_stock.append({
                        "symbol": h["tradingsymbol"],
                        "change": round(change, 2),
                        "prev_close": prev_close,
                        "last_price": last_price,
                        "qty": qty,
                    })
        except Exception:
            pass  # no active session → unrealised stays 0

        # Realised trades: after 5 PM = today, before 9 AM = yesterday
        if now_ist.hour >= 17:
            trade_date = now_ist.strftime("%Y-%m-%d")
        else:
            # Find last trading day for realised lookup
            trade_date = (now_ist - timedelta(days=1)).strftime("%Y-%m-%d")

        realised_result = compute_realised_pnl(trade_date, trade_date)
        realised_daily = realised_result["total_realised_pnl"]

        return {
            "label": label,
            "date": trade_date,
            "unrealised_daily": round(unrealised_daily, 2),
            "realised_daily": round(realised_daily, 2),
            "total_daily_pnl": round(unrealised_daily + realised_daily, 2),
            "stock_count": stock_count,
            "top_movers": sorted(per_stock, key=lambda x: abs(x["change"]), reverse=True)[:5],
        }

    else:
        # ── Delivery cache path (market hours 9 AM – 5 PM) ──
        label = "Yesterday's P&L"

        conn = get_connection()
        cursor = conn.cursor()

        # Get current holding quantities (Kite API or fallback to snapshot)
        holdings_qty = {}
        session_id = request.cookies.get("tf_session")
        try:
            holdings = fetch_zerodha_holdings(session_id)
            for h in holdings:
                holdings_qty[h["tradingsymbol"]] = h["quantity"]
        except Exception:
            conn2 = get_connection()
            c2 = conn2.cursor()
            c2.execute("""
                SELECT tradingsymbol, quantity FROM holdings_snapshots
                WHERE snapshot_at = (SELECT MAX(snapshot_at) FROM holdings_snapshots)
            """)
            for r in c2.fetchall():
                holdings_qty[r["tradingsymbol"]] = r["quantity"]
            conn2.close()

        if not holdings_qty:
            conn.close()
            return {
                "label": label, "date": None,
                "unrealised_daily": 0, "realised_daily": 0,
                "total_daily_pnl": 0, "stock_count": 0, "top_movers": [],
            }

        # For each holding, get its last 2 close prices from delivery_cache
        unrealised_daily = 0.0
        per_stock = []
        ref_date = None

        for sym, qty in holdings_qty.items():
            cursor.execute("""
                SELECT trade_date, close_price FROM delivery_cache
                WHERE symbol = ? ORDER BY trade_date DESC LIMIT 2
            """, (sym,))
            rows = cursor.fetchall()
            if len(rows) < 2:
                continue
            lp = rows[0]["close_price"]
            pp = rows[1]["close_price"]
            day = rows[0]["trade_date"]
            if not ref_date or day > ref_date:
                ref_date = day
            if pp and pp > 0:
                change = (lp - pp) * qty
                unrealised_daily += change
                if abs(change) > 0.01:
                    per_stock.append({
                        "symbol": sym,
                        "change": round(change, 2),
                        "prev_close": pp,
                        "last_price": lp,
                        "qty": qty,
                    })

        conn.close()

        # Realised trades on the reference date
        realised_daily = 0.0
        if ref_date:
            realised_result = compute_realised_pnl(ref_date, ref_date)
            realised_daily = realised_result["total_realised_pnl"]

        return {
            "label": label,
            "date": ref_date,
            "unrealised_daily": round(unrealised_daily, 2),
            "realised_daily": round(realised_daily, 2),
            "total_daily_pnl": round(unrealised_daily + realised_daily, 2),
            "stock_count": len(holdings_qty),
            "top_movers": sorted(per_stock, key=lambda x: abs(x["change"]), reverse=True)[:5],
        }


@router.get("/overview")
def portfolio_overview(request: Request):
    """
    Portfolio summary (Phase 1)
    """
    session_id = request.cookies.get("tf_session")
    try:
        holdings = fetch_zerodha_holdings(session_id)
    except Exception as e:
        raise HTTPException(status_code=403, detail=str(e))

    total_stocks = len(holdings)
    total_quantity = sum(h["quantity"] for h in holdings)
    total_invested = sum(h["average_price"] * h["quantity"] for h in holdings)
    current_value = sum(h["last_price"] * h["quantity"] for h in holdings)
    total_pnl = current_value - total_invested

    return {
        "total_stocks": total_stocks,
        "total_quantity": total_quantity,
        "total_invested_value": round(total_invested, 2),
        "current_value": round(current_value, 2),
        "total_pnl": round(total_pnl, 2),
    }

@router.get("/margins")
def portfolio_margins(request: Request):
    """Available cash and collateral from Zerodha equity margins."""
    session_id = request.cookies.get("tf_session")
    try:
        margins = fetch_zerodha_margins(session_id)
    except Exception as e:
        raise HTTPException(status_code=403, detail=str(e))

    available = margins.get("available", {})
    return {
        "net": round(margins.get("net", 0), 2),
        "cash": round(available.get("cash", 0), 2),
        "collateral": round(available.get("collateral", 0), 2),
        "opening_balance": round(available.get("opening_balance", 0), 2),
        "live_balance": round(available.get("live_balance", 0), 2),
        "intraday_payin": round(available.get("intraday_payin", 0), 2),
    }


@router.get("/holdings")
def portfolio_holdings(request: Request):
    session_id = request.cookies.get("tf_session")
    try:
        holdings = fetch_zerodha_holdings(session_id)
    except Exception as e:
        raise HTTPException(status_code=403, detail=str(e))

    # Ensure instruments exist & enriched
    upsert_instruments_from_holdings(holdings)

    # Get trade counts per symbol from trades table
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT symbol, COUNT(*) as cnt FROM trades GROUP BY symbol")
    trade_counts = {row["symbol"]: row["cnt"] for row in cursor.fetchall()}
    conn.close()

    data = []
    total_invested = 0
    total_current = 0

    for h in holdings:
        invested_value = round(h["average_price"] * h["quantity"], 2)
        current_value = round(h["last_price"] * h["quantity"], 2)
        pnl = round(current_value - invested_value, 2)

        total_invested += invested_value
        total_current += current_value

        # Look up sector from instruments table
        instrument = get_instrument(h["tradingsymbol"], h["exchange"])
        sector = instrument["sector"] if instrument and instrument["sector"] else None

        # Trigger enrichment if sector is still missing
        if not sector and enrich_instrument_if_missing:
            enrich_instrument_if_missing(h["tradingsymbol"], h["exchange"])
            instrument = get_instrument(h["tradingsymbol"], h["exchange"])
            sector = instrument["sector"] if instrument and instrument["sector"] else None

        data.append({
            "symbol": h["tradingsymbol"],
            "exchange": h["exchange"],
            "quantity": h["quantity"],
            "avg_buy_price": h["average_price"],
            "current_price": h["last_price"],
            "invested_value": invested_value,
            "current_value": current_value,
            "pnl": pnl,
            "sector": sector,
            "num_trades": trade_counts.get(h["tradingsymbol"], 0),
        })

    return {
        "count": len(data),
        "data": data,
        "meta": {
            "total_invested": round(total_invested, 2),
            "total_current": round(total_current, 2),
            "total_pnl": round(total_current - total_invested, 2)
        }
    }

@router.get("/historical-holdings")
def historical_holdings(request: Request, fy: str = None):
    """
    Stocks fully exited (total buy qty == total sell qty from trades table),
    excluding any stock currently held in Zerodha.
    Optional FY filter: ?fy=FY2024-25 filters by last_sell_date within that FY.
    """
    from backend.app.services.trades import compute_historical_holdings, get_fy_bounds, get_available_fys

    # Get current holdings symbols to exclude
    session_id = request.cookies.get("tf_session")
    current_symbols = []
    try:
        holdings = fetch_zerodha_holdings(session_id)
        current_symbols = [h["tradingsymbol"] for h in holdings]
    except Exception:
        pass  # If session expired, still show historical data

    # Parse FY filter
    fy_start, fy_end = None, None
    if fy and fy.startswith("FY"):
        fy_start, fy_end = get_fy_bounds(fy)

    data = compute_historical_holdings(current_symbols, fy_start=fy_start, fy_end=fy_end)

    # Step 1: Insert historical symbols into instruments table (INSERT OR IGNORE)
    # so they exist for sector enrichment to work
    conn = get_connection()
    cursor = conn.cursor()
    for item in data:
        cursor.execute("""
            INSERT OR IGNORE INTO instruments (symbol, exchange, isin)
            VALUES (?, ?, ?)
        """, (item["symbol"], item["exchange"], item.get("isin")))
    conn.commit()
    conn.close()

    # Step 2: Enrich with sector info from instruments table + sector_map
    from backend.app.services.sector_map import get_sector_info
    try:
        from backend.app.services.instruments import enrich_instrument_if_missing as _enrich
    except ImportError:
        _enrich = None
    missing_sectors = []
    for item in data:
        instrument = get_instrument(item["symbol"], item["exchange"])
        sector = instrument["sector"] if instrument and instrument["sector"] else None
        if not sector:
            info = get_sector_info(item["symbol"])
            if info.get("sector") and info["sector"] != "Unknown":
                sector = info["sector"]
                # Also persist to instruments table
                from backend.app.services.db import update_instrument_sector
                update_instrument_sector(item["symbol"], item["exchange"],
                                         info["sector"], info.get("industry", "Unknown"))
            else:
                missing_sectors.append((item["symbol"], item["exchange"]))
        item["sector"] = sector

    # Step 3: Background-enrich missing sectors via Yahoo Finance
    # (results available on next page load, skipped on Vercel)
    if missing_sectors and _enrich:
        import threading
        def _bg_enrich(pairs):
            import logging
            log = logging.getLogger("sector_enrich")
            for sym, exch in pairs:
                try:
                    _enrich(sym, exch)
                    log.info(f"Enriched sector for {sym}")
                except Exception:
                    pass
        threading.Thread(target=_bg_enrich, args=(missing_sectors,), daemon=True).start()

    total_pnl = sum(d["total_pnl"] for d in data)

    return {
        "count": len(data),
        "data": data,
        "meta": {
            "total_invested": round(sum(d["total_invested"] for d in data), 2),
            "total_proceeds": round(sum(d["total_proceeds"] for d in data), 2),
            "total_pnl": round(total_pnl, 2),
        },
        "available_fys": get_available_fys(),
    }


@router.get("/sector-allocation")
def sector_allocation(request: Request):
    """
    Aggregated sector allocation — uses live holdings enriched with sector data.
    Falls back to snapshot data only if live fetch fails.
    """
    session_id = request.cookies.get("tf_session")

    # --- Primary path: compute from live holdings (always fresh) ---
    try:
        holdings = fetch_zerodha_holdings(session_id)
        upsert_instruments_from_holdings(holdings)

        sector_map = {}
        for h in holdings:
            instrument = get_instrument(h["tradingsymbol"], h["exchange"])
            sector = instrument["sector"] if instrument and instrument["sector"] else None

            if not sector and enrich_instrument_if_missing:
                enrich_instrument_if_missing(h["tradingsymbol"], h["exchange"])
                instrument = get_instrument(h["tradingsymbol"], h["exchange"])
                sector = instrument["sector"] if instrument and instrument["sector"] else "Unknown"

            if sector not in sector_map:
                sector_map[sector] = {"current": 0, "invested": 0, "pnl": 0}

            invested = h["average_price"] * h["quantity"]
            current = h["last_price"] * h["quantity"]
            sector_map[sector]["invested"] += invested
            sector_map[sector]["current"] += current
            sector_map[sector]["pnl"] += current - invested

        total_current = sum(v["current"] for v in sector_map.values()) or 1
        total_invested = sum(v["invested"] for v in sector_map.values()) or 1

        by_current_value = []
        by_invested_value = []

        for sector, v in sector_map.items():
            by_current_value.append({
                "sector": sector,
                "value": round(v["current"], 2),
                "percentage": round((v["current"] / total_current) * 100, 2),
                "profit": round(v["pnl"], 2)
            })
            by_invested_value.append({
                "sector": sector,
                "value": round(v["invested"], 2),
                "percentage": round((v["invested"] / total_invested) * 100, 2)
            })

        return {
            "by_current_value": by_current_value,
            "by_invested_value": by_invested_value
        }

    except Exception:
        pass  # Fall through to snapshot-based approach

    # --- Fallback: snapshot-based (if live fails) ---
    conn = get_connection()
    cursor = conn.cursor()

    query = """
    SELECT
        i.sector AS sector,
        SUM(h.quantity * h.last_price) AS current_value,
        SUM(h.quantity * h.average_price) AS invested_value,
        SUM(h.pnl) AS pnl
    FROM holdings_snapshots h
    JOIN instruments i
      ON h.tradingsymbol = i.symbol
     AND h.exchange = i.exchange
    WHERE h.snapshot_at = (
        SELECT MAX(snapshot_at) FROM holdings_snapshots
    )
    GROUP BY i.sector
    """

    cursor.execute(query)
    rows = cursor.fetchall()
    conn.close()

    total_current = sum(row["current_value"] for row in rows) or 1
    total_invested = sum(row["invested_value"] for row in rows) or 1

    by_current_value = []
    by_invested_value = []

    for r in rows:
        by_current_value.append({
            "sector": r["sector"] or "Unknown",
            "value": round(r["current_value"], 2),
            "percentage": round((r["current_value"] / total_current) * 100, 2),
            "profit": round(r["pnl"], 2)
        })

        by_invested_value.append({
            "sector": r["sector"] or "Unknown",
            "value": round(r["invested_value"], 2),
            "percentage": round((r["invested_value"] / total_invested) * 100, 2)
        })

    return {
        "by_current_value": by_current_value,
        "by_invested_value": by_invested_value
    }

@router.get("/delivery-data")
def delivery_data(symbol: str, period: str = "1y"):
    """
    Fetch delivery volume data for a single NSE stock.
    Serves from DB cache (populated by sync). Falls back to live NSE if cache empty.
    """
    from backend.app.services.delivery import fetch_delivery_data

    from datetime import datetime as _dt
    period_map = {"3m": 90, "6m": 180, "1y": 365, "2y": 730, "3y": 1095}
    if period == "all":
        period_days = (_dt.now() - _dt(2020, 1, 1)).days
    else:
        period_days = period_map.get(period, 365)

    try:
        data = fetch_delivery_data(symbol, period_days)
    except Exception:
        data = []

    return {
        "symbol": symbol,
        "period": period,
        "count": len(data),
        "data": data
    }


@router.post("/delivery-data/sync")
def sync_delivery_data(request: Request, period: str = "all"):
    """
    Sync delivery data for ALL holdings from NSE into DB cache.
    Call this from local machine daily (NSE blocks cloud IPs).
    """
    from backend.app.services.delivery import fetch_and_cache_delivery

    session_id = request.cookies.get("tf_session")

    # Get all unique symbols from current holdings (any exchange —
    # NSE delivery data may exist even for BSE-listed stocks)
    try:
        holdings = fetch_zerodha_holdings(session_id)
    except Exception:
        raise HTTPException(status_code=401, detail="No active Zerodha session")

    from datetime import datetime as _dt
    period_map = {"3m": 90, "6m": 180, "1y": 365, "2y": 730, "3y": 1095}
    if period == "all":
        period_days = (_dt.now() - _dt(2020, 1, 1)).days
    else:
        period_days = period_map.get(period, 365)

    all_symbols = list(set(h["tradingsymbol"] for h in holdings))

    results = {}
    for sym in all_symbols:
        try:
            data = fetch_and_cache_delivery(sym, period_days)
            results[sym] = len(data)
        except Exception as e:
            results[sym] = f"error: {str(e)}"

    return {
        "synced": len(all_symbols),
        "period": period,
        "results": results
    }


# ─── Per-Symbol Trades (for chart markers) ─────────────────────────

@router.get("/trades")
def get_trades_by_symbol(symbol: str):
    """
    Return individual trade records for a symbol, grouped by date.
    Used by the frontend to render buy/sell markers and variable-density
    fill on the price line chart.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT trade_date, trade_type, quantity, price
        FROM trades
        WHERE symbol = ?
        ORDER BY trade_date ASC, order_execution_time ASC
    """, (symbol,))
    rows = cursor.fetchall()
    conn.close()

    # Group by date, separate buys and sells
    from collections import OrderedDict
    by_date = OrderedDict()
    for r in rows:
        d = r["trade_date"]
        if d not in by_date:
            by_date[d] = {"buys": [], "sells": []}
        entry = {"quantity": r["quantity"], "price": r["price"]}
        if r["trade_type"] == "buy":
            by_date[d]["buys"].append(entry)
        else:
            by_date[d]["sells"].append(entry)

    trades = []
    cumulative_qty = []
    running_qty = 0

    for date, info in by_date.items():
        buy_qty = sum(b["quantity"] for b in info["buys"])
        sell_qty = sum(s["quantity"] for s in info["sells"])
        net = buy_qty - sell_qty
        running_qty += net

        trades.append({
            "date": date,
            "buys": info["buys"],
            "sells": info["sells"],
            "net_qty": round(net, 2),
        })
        cumulative_qty.append({
            "date": date,
            "qty_after": round(running_qty, 2),
        })

    return {
        "symbol": symbol,
        "count": len(trades),
        "trades": trades,
        "cumulative_qty": cumulative_qty,
    }


# ─── Trades Import & Realised P&L ────────────────────────────────────

@router.post("/trades/import")
def import_trades():
    """Import all tradebook CSVs into trades table. Idempotent."""
    from backend.app.services.trades import import_tradebooks
    summary = import_tradebooks()
    total = sum(summary.values())
    return {"status": "ok", "total_imported": total, "by_file": summary}


@router.get("/realised-pnl")
def realised_pnl(fy: str = None):
    """
    Realised P&L computed via FIFO.

    Returns YTD (current FY to today), previous FY, and optionally a
    specific FY if ?fy=FY2022-23 is provided.
    """
    from backend.app.services.trades import (
        compute_realised_pnl,
        get_fy_bounds,
        get_available_fys,
    )
    from datetime import datetime as _dt

    today = _dt.now().strftime("%Y-%m-%d")

    # Current FY bounds
    current_fy_start, current_fy_end = get_fy_bounds()
    current_fy_label = f"FY{current_fy_start[:4]}-{str(int(current_fy_start[:4]) + 1)[-2:]}"

    # YTD = current FY start → today
    ytd_result = compute_realised_pnl(current_fy_start, today)

    # Previous FY
    prev_start_year = int(current_fy_start[:4]) - 1
    prev_fy_start = f"{prev_start_year}-04-01"
    prev_fy_end = f"{prev_start_year + 1}-03-31"
    prev_fy_label = f"FY{prev_start_year}-{str(prev_start_year + 1)[-2:]}"
    prev_fy_result = compute_realised_pnl(prev_fy_start, prev_fy_end)

    # Specific FY (optional query param)
    specific_fy = None
    if fy and fy.startswith("FY"):
        fy_s, fy_e = get_fy_bounds(fy)
        specific_result = compute_realised_pnl(fy_s, fy_e)
        specific_fy = {
            "label": fy,
            "realised_pnl": specific_result["total_realised_pnl"],
            "total_sells": specific_result["total_sells"],
            "symbols_sold": specific_result["total_symbols_sold"],
            "by_symbol": specific_result["by_symbol"],
        }

    available = get_available_fys()

    return {
        "ytd": {
            "label": f"YTD ({current_fy_label})",
            "realised_pnl": ytd_result["total_realised_pnl"],
            "total_sells": ytd_result["total_sells"],
            "symbols_sold": ytd_result["total_symbols_sold"],
        },
        "previous_fy": {
            "label": prev_fy_label,
            "realised_pnl": prev_fy_result["total_realised_pnl"],
            "total_sells": prev_fy_result["total_sells"],
            "symbols_sold": prev_fy_result["total_symbols_sold"],
        },
        "available_fys": available,
        "specific_fy": specific_fy,
    }


# ─── Trade Sync (Kite API → trades table) ─────────────────────────────

@router.get("/trade-sync/status")
def trade_sync_status():
    """Return scheduler status and next run times."""
    return get_scheduler_status()


@router.post("/trade-sync/trigger")
def trade_sync_trigger(request: Request):
    """Manually trigger a trade sync using the current session's token."""
    session_id = request.cookies.get("tf_session")
    token = get_active_access_token(session_id) if session_id else None
    result = sync_trades_from_kite(access_token=token)
    return result
