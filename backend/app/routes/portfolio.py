from fastapi import APIRouter, HTTPException

from backend.app.services.zerodha_holdings import fetch_zerodha_holdings
from backend.app.services.db import (
    get_latest_snapshot_meta,
    upsert_instruments_from_holdings,
    get_instrument
)
from backend.app.services.instruments import enrich_instrument_if_missing

router = APIRouter(prefix="/portfolio", tags=["Portfolio"])


@router.get("/holdings")
def portfolio_holdings():
    """
    Phase 2:
    - Live holdings from Zerodha
    - Instruments auto-upserted
    - Sector & industry auto-enriched (cached)
    - Snapshot metadata attached
    - Sector & industry returned in response
    """

    # 1️⃣ Fetch live holdings
    try:
        holdings = fetch_zerodha_holdings()
    except Exception as e:
        raise HTTPException(status_code=403, detail=str(e))

    # 2️⃣ Ensure instruments exist (idempotent)
    upsert_instruments_from_holdings(holdings)

    data = []

    # 3️⃣ Enrich, read back from DB, shape response
    for h in holdings:
        symbol = h["tradingsymbol"]
        exchange = h["exchange"]

        # 🔹 Ensure enrichment (runs only once per symbol)
        enrich_instrument_if_missing(symbol, exchange)

        # 🔹 READ instrument metadata from DB
        instrument = get_instrument(symbol, exchange)

        invested_value = h["average_price"] * h["quantity"]
        current_value = h["last_price"] * h["quantity"]

        snapshot_meta = get_latest_snapshot_meta(symbol)

        data.append({
            "symbol": symbol,
            "exchange": exchange,
            "sector": instrument["sector"],
            "industry": instrument["industry"],
            "quantity": h["quantity"],
            "avg_buy_price": h["average_price"],
            "current_price": h["last_price"],
            "invested_value": round(invested_value, 2),
            "current_value": round(current_value, 2),
            "pnl": round(h["pnl"], 2),
            "last_snapshot_at": snapshot_meta["last_snapshot_at"],
            "snapshot_count": snapshot_meta["snapshot_count"]
        })

    return {
        "count": len(data),
        "data": data
    }
