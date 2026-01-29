from fastapi import FastAPI
from backend.app.auth.zerodha import router as zerodha_auth_router

app = FastAPI(
    title="TuneFolio API",
    description="Authentication and portfolio intelligence backend",
    version="0.1.0"
)

app.include_router(zerodha_auth_router, prefix="/auth/zerodha")

@app.get("/")
def health_check():
    return {"status": "TuneFolio backend running"}
