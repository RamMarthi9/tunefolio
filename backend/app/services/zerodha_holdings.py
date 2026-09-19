import os
import time
import logging
import requests
from fastapi import HTTPException
from backend.app.services.db import get_active_access_token, save_holdings_snapshot, deactivate_session

logger = logging.getLogger(__name__)

# Per-session cache: keyed by session_id so different users don't share data
_holdings_cache = {}
_margins_cache = {}
CACHE_TTL = 30  # seconds


def fetch_zerodha_holdings(session_id: str = None):
    if not get_active_access_token(session_id):
        raise HTTPException(status_code=401, detail="Session expired. Please reconnect.")
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
        headers=headers, timeout=15
    )

    if response.status_code in (401, 403):
        deactivate_session(session_id)
        _holdings_cache.pop(cache_key, None)
        _margins_cache.pop(cache_key, None)
        raise HTTPException(status_code=401, detail="Broker session expired. Please reconnect.")
    if response.status_code != 200:
        logger.warning("Kite holdings API unavailable: HTTP %s", response.status_code)
        raise HTTPException(
            status_code=502,
            detail="Broker holdings are temporarily unavailable"
        )

    holdings = response.json()["data"]

    # Persist snapshot
    save_holdings_snapshot(holdings)

    from backend.app.services.performance import observe_holdings
    from datetime import datetime, timezone
    observe_holdings(holdings, datetime.fromtimestamp(now, timezone.utc).isoformat())

    # Update per-session cache
    _holdings_cache[cache_key] = {"data": holdings, "timestamp": now}

    return holdings


def fetch_zerodha_margins(session_id: str = None):
    if not get_active_access_token(session_id):
        raise HTTPException(status_code=401, detail="Session expired. Please reconnect.")
    now = time.time()

    cache_key = session_id or "__global__"
    if cache_key in _margins_cache:
        entry = _margins_cache[cache_key]
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
        "https://api.kite.trade/user/margins/equity",
        headers=headers, timeout=15
    )

    if response.status_code in (401, 403):
        deactivate_session(session_id)
        _holdings_cache.pop(cache_key, None)
        _margins_cache.pop(cache_key, None)
        raise HTTPException(status_code=401, detail="Broker session expired. Please reconnect.")
    if response.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail="Failed to fetch Zerodha margins"
        )

    margins = response.json()["data"]

    _margins_cache[cache_key] = {"data": margins, "timestamp": now}

    return margins


def holdings_freshness(session_id):
    from datetime import datetime, timezone
    entry = _holdings_cache.get(session_id)
    return {"source": "broker", "retrieved_at": datetime.fromtimestamp(entry["timestamp"], timezone.utc).isoformat() if entry else None,
            "quote_at": None, "cache_ttl_seconds": CACHE_TTL}
