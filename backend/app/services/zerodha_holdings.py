import os
import time
import requests
from fastapi import HTTPException
from backend.app.services.db import get_active_access_token, save_holdings_snapshot

# In-memory cache: avoids duplicate Zerodha API calls within the same page load
_holdings_cache = {"data": None, "timestamp": 0}
CACHE_TTL = 30  # seconds — holdings don't change faster than this


def fetch_zerodha_holdings():
    now = time.time()

    # Return cached data if fresh
    if _holdings_cache["data"] and (now - _holdings_cache["timestamp"]) < CACHE_TTL:
        return _holdings_cache["data"]

    access_token = get_active_access_token()

    if not access_token:
        raise HTTPException(
            status_code=401,
            detail="No active Zerodha session found"
        )

    KITE_API_KEY = os.getenv("KITE_API_KEY")

    headers = {
        "Authorization": f"token {KITE_API_KEY}:{access_token}"
    }

    response = requests.get(
        "https://api.kite.trade/portfolio/holdings",
        headers=headers
    )

    if response.status_code != 200:
        raise HTTPException(
            status_code=response.status_code,
            detail="Failed to fetch Zerodha holdings"
        )

    holdings = response.json()["data"]

    # Persist snapshot
    save_holdings_snapshot(holdings)

    # Update cache
    _holdings_cache["data"] = holdings
    _holdings_cache["timestamp"] = now

    return holdings
