import os
import requests
import hashlib
import threading
import secrets
from urllib.parse import urlencode
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query, Response, Request
from dotenv import load_dotenv
from fastapi.responses import RedirectResponse
from backend.app.services.db import save_zerodha_session, deactivate_session, create_login_state, consume_login_state

# Compute .env path relative to this file (backend/app/auth/ -> backend/)
_env_path = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(dotenv_path=_env_path)

router = APIRouter()

KITE_API_KEY = os.getenv("KITE_API_KEY")
KITE_API_SECRET = os.getenv("KITE_API_SECRET")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://127.0.0.1:8000")


@router.get("/callback")
def zerodha_callback(request: Request, request_token: str = Query(None), state: str = Query(None)):
    if not request_token:
        raise HTTPException(status_code=400, detail="Missing request token")
    cookie_state = request.cookies.get('tf_login_state')
    if not state or not cookie_state or not secrets.compare_digest(state, cookie_state) or not consume_login_state(state):
        raise HTTPException(status_code=400, detail="Login verification expired or invalid. Start login again on the configured callback domain.")

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

    response = requests.post(session_url, data=payload, timeout=15)

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

    # Background work receives an explicit account; thread-local context is not inherited.
    from backend.app.services.trade_sync import sync_trades_from_kite
    if not os.getenv('VERCEL'):
        threading.Thread(target=sync_trades_from_kite,
                         args=(access_token, user_id), daemon=True).start()
    # On Vercel, sync in a separate account-bound request instead of a frozen thread.

    # Set session cookie and redirect to frontend
    redirect = RedirectResponse(
        url="/?status=connected",
        status_code=302
    )
    # Session cookie: no Max-Age/Expires = browser-session cookie (deleted on browser close)
    redirect.set_cookie(
        key="tf_session",
        value=session_id,
        httponly=True,
        samesite="lax",
        secure=os.getenv('ENVIRONMENT') == 'production' or request.url.scheme == "https",
        path="/"
    )
    redirect.delete_cookie('tf_login_state', path='/auth/zerodha')
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
def zerodha_login(request: Request):
    state = create_login_state()
    query = urlencode({'api_key': KITE_API_KEY, 'v': '3',
                       'redirect_params': urlencode({'state': state})})
    response = RedirectResponse(url='https://kite.zerodha.com/connect/login?' + query)
    response.set_cookie('tf_login_state', state, max_age=600, httponly=True,
                        secure=os.getenv('ENVIRONMENT') == 'production' or request.url.scheme == 'https',
                        samesite='lax', path='/auth/zerodha')
    return response
