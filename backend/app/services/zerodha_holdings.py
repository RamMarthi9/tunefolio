import os
import time
import requests
from fastapi import HTTPException
from backend.app.services.db import get_active_access_token, save_holdings_snapshot

# Per-session cache: keyed by session_id so different users don't share data
_holdings_cache = {}
CACHE_TTL = 30  # seconds


def fetch_zerodha_holdings(session_id: str = None):
    now = time.time()

    # Return cached data if fresh (per session)
    cache_key = session_id or "__global__"
    if cache_key in _holdings_cache:
        entry = _holdings_cache[cache_key]
        if entry["data"] and (now - entry["timestamp"]) < CACHE_TTL:
            return entry["data"]

    access_token = get_active_access_token(session_id)

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

    # Update per-session cache
    _holdings_cache[cache_key] = {"data": holdings, "timestamp": now}

    return holdings
