import os
import requests
import hashlib
import threading
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query, Response, Request
from dotenv import load_dotenv
from fastapi.responses import RedirectResponse
from backend.app.services.db import save_zerodha_session, deactivate_session

# Compute .env path relative to this file (backend/app/auth/ -> backend/)
_env_path = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(dotenv_path=_env_path)

router = APIRouter()

KITE_API_KEY = os.getenv("KITE_API_KEY")
KITE_API_SECRET = os.getenv("KITE_API_SECRET")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://127.0.0.1:8000")


@router.get("/callback")
def zerodha_callback(request_token: str = Query(None)):
    if not request_token:
        raise HTTPException(status_code=400, detail="Missing request token")

    # Generate checksum
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

    # Save session and get back the session_id
    session_id = save_zerodha_session(
        user_id=user_id,
        access_token=access_token
    )

    # Fire-and-forget trade sync with the fresh token
    from backend.app.services.trade_sync import sync_trades_from_kite
    threading.Thread(target=sync_trades_from_kite, args=(access_token,), daemon=True).start()

    # Set session cookie and redirect to frontend
    redirect = RedirectResponse(
        url=f"{FRONTEND_URL}/?status=connected",
        status_code=302
    )
    # Session cookie: no Max-Age/Expires = browser-session cookie (deleted on browser close)
    redirect.set_cookie(
        key="tf_session",
        value=session_id,
        httponly=True,
        samesite="lax",
        secure=FRONTEND_URL.startswith("https"),
        path="/"
    )
    return redirect


@router.post("/logout")
def zerodha_logout(request: Request):
    session_id = request.cookies.get("tf_session")
    if session_id:
        deactivate_session(session_id)

    resp = Response(content='{"status":"logged_out"}', media_type="application/json")
    resp.delete_cookie("tf_session", path="/")
    return resp


@router.get("/login")
def zerodha_login():
    login_url = (
        f"https://kite.trade/connect/login"
        f"?api_key={KITE_API_KEY}&v=3"
    )
    return RedirectResponse(url=login_url)
