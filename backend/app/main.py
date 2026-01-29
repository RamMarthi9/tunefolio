from fastapi import FastAPI
from backend.app.auth.zerodha import router as zerodha_auth_router
from backend.app.services.db import init_db, init_holdings_snapshot_table
from backend.app.services.sessions import router as session_router
from backend.app.services.holdings import router as holdings_router

app = FastAPI(
    title="TuneFolio API",
    description="Authentication and portfolio intelligence backend",
    version="0.1.0"
)

app.include_router(zerodha_auth_router, prefix="/auth/zerodha")
app.include_router(session_router)
app.include_router(holdings_router)

@app.on_event("startup")
def startup_event():
    init_db()
    init_holdings_snapshot_table()

@app.get("/")
def health_check():
    return {"status": "TuneFolio backend running"}


