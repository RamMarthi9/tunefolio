from fastapi import FastAPI
from backend.app.auth.zerodha import router as zerodha_auth_router
from backend.app.services.db import init_db

app = FastAPI(
    title="TuneFolio API",
    description="Authentication and portfolio intelligence backend",
    version="0.1.0"
)

app.include_router(zerodha_auth_router, prefix="/auth/zerodha")

@app.on_event("startup")
def startup_event():
    init_db()

@app.get("/")
def health_check():
    return {"status": "TuneFolio backend running"}

