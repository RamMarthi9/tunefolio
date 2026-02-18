from fastapi import APIRouter
from backend.app.services.zerodha_holdings import fetch_zerodha_holdings

router = APIRouter()


@router.get("/holdings")
def get_holdings():
    return fetch_zerodha_holdings()

