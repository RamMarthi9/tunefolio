import requests
from fastapi import HTTPException
from backend.app.services.db import get_active_access_token


def fetch_zerodha_holdings():
    access_token = get_active_access_token()

    if not access_token:
        raise HTTPException(
            status_code=401,
            detail="No active Zerodha session found"
        )

    import os

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

    return response.json()["data"]
