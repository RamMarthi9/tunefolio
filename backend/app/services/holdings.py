from fastapi import APIRouter, Request
from backend.app.services.zerodha_holdings import fetch_zerodha_holdings

router = APIRouter()


@router.get("/holdings")
def get_holdings(request: Request):
    return fetch_zerodha_holdings(request.cookies.get("tf_session"))

