from fastapi import APIRouter, HTTPException, Request

from backend.app.services.zerodha_holdings import fetch_zerodha_holdings
from backend.app.services.db import (
    get_latest_snapshot_meta,
    upsert_instruments_from_holdings,
    get_instrument
)
from backend.app.services.instruments import enrich_instrument_if_missing
from backend.app.services.db import get_connection

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

    # Get all unique NSE symbols from current holdings
    try:
        holdings = fetch_zerodha_holdings(session_id)
    except Exception:
        raise HTTPException(status_code=401, detail="No active Zerodha session")

    period_map = {"1y": 365, "6m": 180, "3m": 90}
    period_days = period_map.get(period, 365)

    nse_symbols = list(set(
        h["tradingsymbol"] for h in holdings
        if h.get("exchange") == "NSE"
    ))

    results = {}
    for sym in nse_symbols:
        try:
            data = fetch_and_cache_delivery(sym, period_days)
            results[sym] = len(data)
        except Exception as e:
            results[sym] = f"error: {str(e)}"

    return {
        "synced": len(nse_symbols),
        "period": period,
        "results": results
    }
