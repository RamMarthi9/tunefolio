from fastapi import APIRouter, HTTPException, Request
from backend.app.services.db import get_active_zerodha_session

router = APIRouter()


@router.get("/session/active")
def fetch_active_session(request: Request):
    session_id = request.cookies.get("tf_session")
    session = get_active_zerodha_session(session_id)

    if not session:
        raise HTTPException(
            status_code=404,
            detail="No active Zerodha session found"
        )

    return session
