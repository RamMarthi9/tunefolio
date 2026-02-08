import os
import requests
import hashlib
from fastapi import APIRouter, HTTPException, Query
from dotenv import load_dotenv
from fastapi.responses import RedirectResponse
from backend.app.services.db import save_zerodha_session

load_dotenv(dotenv_path="backend/.env")

router = APIRouter()

KITE_API_KEY = os.getenv("KITE_API_KEY")
KITE_API_SECRET = os.getenv("KITE_API_SECRET")


@router.get("/callback")
def zerodha_callback(request_token: str = Query(None)):
    if not request_token:
        raise HTTPException(status_code=400, detail="Missing request token")

    # ✅ Generate checksum (CRITICAL)
    checksum = hashlib.sha256(
        f"{KITE_API_KEY}{request_token}{KITE_API_SECRET}".encode()
    ).hexdigest()

    session_url = "https://api.kite.trade/session/token"

    payload = {
        "api_key": KITE_API_KEY,
        "request_token": request_token,
        "checksum": checksum
    }

    response = requests.post(session_url, data=payload)

    if response.status_code != 200:
        raise HTTPException(
            status_code=401,
            detail="Failed to authenticate with Zerodha"
        )

    data = response.json()["data"]

    user_id = data["user_id"]
    access_token = data["access_token"]

    # ✅ Save token securely in SQLite
    save_zerodha_session(
        user_id=user_id,
        access_token=access_token
    )

    frontend_success_url = "http://127.0.0.1:5500/success.html"

    return RedirectResponse(
        url=f"{frontend_success_url}?status=connected",
        status_code=302
    )

@router.get("/login")
def zerodha_login():
    login_url = (
        f"https://kite.trade/connect/login"
        f"?api_key={KITE_API_KEY}&v=3"
    )
    return RedirectResponse(url=login_url)
