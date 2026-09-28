from fastapi import FastAPI, HTTPException
from pathlib import Path
from assistant_core.services.proactivity import ProactivityRequest, ProactivityService
from assistant_core.services.file_evidence import FileAccessRequest

from cleo_api.routes.chat import router as chat_router
from cleo_api.routes.chat import orchestrator


app = FastAPI(
    title="Cleo Assistant API",
    version="0.1.0",
    description="Backend API for a cross-platform personal AI assistant.",
)

app.include_router(chat_router)
proactivity = ProactivityService(Path(orchestrator.settings.state_file_path).expanduser().resolve().with_name("proactivity.sqlite3"))


@app.post("/proactivity")
def proactive_update(request: ProactivityRequest) -> dict:
    try:
        return proactivity.handle(request.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/file-access")
def file_access(request: FileAccessRequest) -> dict:
    try:
        return orchestrator.file_evidence.handle(request.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/capability-status")
def capability_status() -> dict:
    return orchestrator.get_capability_status()


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
