from fastapi import APIRouter, HTTPException, Request

from backend.app.services.zerodha_holdings import fetch_zerodha_holdings, fetch_zerodha_margins
from backend.app.services.db import (
    get_latest_snapshot_meta,
    upsert_instruments_from_holdings,
    get_instrument,
    get_active_access_token,
)
from backend.app.services.instruments import enrich_instrument_if_missing
from backend.app.services.db import get_connection
from backend.app.services.trade_sync import sync_trades_from_kite
from backend.app.services.scheduler import get_scheduler_status

router = APIRouter(prefix="/portfolio", tags=["Portfolio"])

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
        if not sector:
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
            "sector": sector
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
def historical_holdings(request: Request):
    """
    Stocks fully exited (total buy qty == total sell qty from trades table),
    excluding any stock currently held in Zerodha.
    """
    from backend.app.services.trades import compute_historical_holdings

    # Get current holdings symbols to exclude
    session_id = request.cookies.get("tf_session")
    current_symbols = []
    try:
        holdings = fetch_zerodha_holdings(session_id)
        current_symbols = [h["tradingsymbol"] for h in holdings]
    except Exception:
        pass  # If session expired, still show historical data

    data = compute_historical_holdings(current_symbols)

    # Enrich with sector info
    for item in data:
        instrument = get_instrument(item["symbol"], item["exchange"])
        sector = instrument["sector"] if instrument and instrument["sector"] else None
        if not sector:
            enrich_instrument_if_missing(item["symbol"], item["exchange"])
            instrument = get_instrument(item["symbol"], item["exchange"])
            sector = instrument["sector"] if instrument and instrument["sector"] else None
        item["sector"] = sector

    total_pnl = sum(d["total_pnl"] for d in data)

    return {
        "count": len(data),
        "data": data,
        "meta": {
            "total_invested": round(sum(d["total_invested"] for d in data), 2),
            "total_proceeds": round(sum(d["total_proceeds"] for d in data), 2),
            "total_pnl": round(total_pnl, 2),
        }
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

            if not sector:
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

    period_map = {"1y": 365, "6m": 180, "3m": 90}
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
def sync_delivery_data(request: Request, period: str = "1y"):
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

    period_map = {"1y": 365, "6m": 180, "3m": 90}
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
