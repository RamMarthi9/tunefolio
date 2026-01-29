import os
import requests
from fastapi import APIRouter, HTTPException, Query
from dotenv import load_dotenv

load_dotenv()

router = APIRouter()
KITE_API_KEY = os.getenv("KITE_API_KEY")
KITE_API_SECRET = os.getenv("KITE_API_SECRET")

@router.get("/callback")
def zerodha_callback(request_token: str = Query(None)):
    if not request_token:
        raise HTTPException(status_code=400, detail="Missing request token")

    session_url = "https://api.kite.trade/session/token"

    payload = {
        "api_key": KITE_API_KEY,
        "request_token": request_token,
        "api_secret": KITE_API_SECRET
    }

    response = requests.post(session_url, data=payload)

    if response.status_code != 200:
        raise HTTPException(status_code=401, detail="Failed to authenticate with Zerodha")

    return {
        "message": "Zerodha authentication successful",
        "status": "connected"
    }
