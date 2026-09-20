import os
import logging
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)

# Load .env early (before any module reads env vars)
_env_path = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(dotenv_path=_env_path)

from backend.app.auth.zerodha import router as zerodha_auth_router
from backend.app.services.db import init_db, init_holdings_snapshot_table, create_delivery_cache_table, create_trades_table, create_index_cache_table
# Lazy import — scheduler uses APScheduler which is not available on Vercel
if not os.getenv("VERCEL"):
    from backend.app.services.scheduler import start_scheduler, stop_scheduler
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

from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from backend.app.services.db import storage_ready, account_scope, init_account, get_active_zerodha_session, system_connection

@app.middleware("http")
async def account_boundary(request, call_next):
    path = request.url.path
    private = path.startswith("/portfolio/") or path == "/holdings"
    session_path = path.startswith("/session/") or path.startswith("/auth/")
    if private or session_path:
        try:
            if not storage_ready():
                raise RuntimeError('Storage missing')
            await run_in_threadpool(ensure_system_schema)
        except Exception:
            return JSONResponse({"detail": "Durable storage is unavailable. Retry shortly."}, status_code=503,
                                headers={"Cache-Control": "no-store"})
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
            return JSONResponse({"detail": "Cross-origin write rejected"}, status_code=403)
    if private:
        session = await run_in_threadpool(get_active_zerodha_session, request.cookies.get("tf_session"))
        if not session:
            return JSONResponse({"detail": "Session expired. Please reconnect."}, status_code=401,
                                headers={"Cache-Control": "no-store"})
        request.state.account_id = session["user_id"]
        with account_scope(session["user_id"]):
            await run_in_threadpool(init_account)
            response = await call_next(request)
    else:
        response = await call_next(request)
    if private or session_path:
        response.headers["Cache-Control"] = "no-store"
    return response

# API routes (MUST be registered BEFORE static files mount)
app.include_router(zerodha_auth_router, prefix="/auth/zerodha")
app.include_router(session_router)
app.include_router(holdings_router)
app.include_router(portfolio_router)

from threading import Lock
_schema_lock = Lock()
_system_schema_key = None

def ensure_system_schema():
    # ASGI lifespan is not guaranteed by every serverless adapter.
    from backend.app.services import db
    global _system_schema_key
    key = (os.getenv('TURSO_DATABASE_URL'), str(db.DATA_ROOT))
    with _schema_lock:
        if _system_schema_key != key:
            init_db()
            _system_schema_key = key

@app.on_event("startup")
def startup_event():
    try:
        if storage_ready():
            ensure_system_schema()
            if not os.getenv("VERCEL") and os.getenv("TUNEFOLIO_DISABLE_SCHEDULER") != "1":
                start_scheduler()
    except Exception:
        logging.error('Durable storage startup failed; private requests will return unavailable')

@app.on_event("shutdown")
def shutdown_event():
    if not os.getenv("VERCEL"):
        stop_scheduler()

@app.get("/api/health")
def health_check():
    try:
        if not storage_ready():
            raise RuntimeError('Storage missing')
        ensure_system_schema()
        conn = system_connection()
        try:
            conn.execute('SELECT 1').fetchone()
        finally:
            conn.close()
    except Exception:
        return JSONResponse({"status": "unavailable", "storage": "unavailable"}, status_code=503,
                            headers={"Cache-Control": "no-store"})
    return JSONResponse({"status": "ok", "storage": "reachable"}, headers={"Cache-Control": "no-store"})

# Serve frontend static files (MUST be LAST — acts as catch-all)
# Skip on Vercel — static files are served by Vercel CDN from public/
if not os.getenv("VERCEL"):
    _frontend_dir = Path(__file__).resolve().parents[2] / "frontend"
    if _frontend_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(_frontend_dir), html=True), name="frontend")
