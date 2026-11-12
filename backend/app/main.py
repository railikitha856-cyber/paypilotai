from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routes.paypal import router as paypal_router
from app.routes.ai import router as ai_router
from app.routes.analytics import router as analytics_router
from app.routes.fraud import router as fraud_router
from app.routes.assistant import router as assistant_router

app = FastAPI(
    title="PayPilot AI API",
    description="Backend API for the PayPilot AI financial operations assistant.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "https://paypilotai-hy67.onrender.com"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(paypal_router)
app.include_router(ai_router)
app.include_router(analytics_router)
app.include_router(fraud_router)
app.include_router(assistant_router)


@app.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    """Return basic API status. No external integrations are configured yet."""
    return {"status": "healthy", "service": "PayPilot AI API"}
