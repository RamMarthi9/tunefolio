from fastapi import APIRouter, HTTPException
from backend.app.services.db import get_active_zerodha_session

router = APIRouter()


@router.get("/session/active")
def fetch_active_session():
    session = get_active_zerodha_session()

    if not session:
        raise HTTPException(
            status_code=404,
            detail="No active Zerodha session found"
        )

    return session
