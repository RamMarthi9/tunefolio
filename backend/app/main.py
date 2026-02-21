import os
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

# Load .env early (before any module reads env vars)
_env_path = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(dotenv_path=_env_path)

from backend.app.auth.zerodha import router as zerodha_auth_router
from backend.app.services.db import init_db, init_holdings_snapshot_table, create_delivery_cache_table, create_trades_table
from backend.app.services.sessions import router as session_router
from backend.app.services.holdings import router as holdings_router
from backend.app.services.db import create_instruments_table
from backend.app.routes.portfolio import router as portfolio_router

app = FastAPI(
    title="TuneFolio API",
    description="Authentication and portfolio intelligence backend",
    version="0.1.0"
)

# CORS: permissive in dev, restricted in prod
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://127.0.0.1:8000")
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")

allowed_origins = ["*"] if ENVIRONMENT == "development" else [FRONTEND_URL]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routes (MUST be registered BEFORE static files mount)
app.include_router(zerodha_auth_router, prefix="/auth/zerodha")
app.include_router(session_router)
app.include_router(holdings_router)
app.include_router(portfolio_router)

@app.on_event("startup")
def startup_event():
    init_db()
    init_holdings_snapshot_table()
    create_instruments_table()
    create_delivery_cache_table()
    create_trades_table()

@app.get("/api/health")
def health_check():
    return {"status": "TuneFolio backend running"}

# Serve frontend static files (MUST be LAST — acts as catch-all)
_frontend_dir = Path(__file__).resolve().parents[2] / "frontend"
if _frontend_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(_frontend_dir), html=True), name="frontend")
