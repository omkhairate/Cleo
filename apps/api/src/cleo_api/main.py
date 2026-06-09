from fastapi import FastAPI

from cleo_api.routes.chat import router as chat_router
from cleo_api.routes.chat import orchestrator


app = FastAPI(
    title="Cleo Assistant API",
    version="0.1.0",
    description="Backend API for a cross-platform personal AI assistant.",
)

app.include_router(chat_router)


@app.get("/health")
def health() -> dict[str, str]:
    model_status = orchestrator.get_model_status()
    return {
        "status": "ok",
        "model_status": model_status["status"],
        "routing_mode": model_status.get("routing_mode", "unknown"),
        "local_provider": model_status.get("local_provider", "unknown"),
        "local_model": model_status.get("local_model", "unknown"),
        "online_provider": model_status.get("online_provider", "unknown"),
        "online_model": model_status.get("online_model", "unknown"),
    }
