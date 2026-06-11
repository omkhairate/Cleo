from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Cleo"
    env: str = "development"
    api_url: str = "http://127.0.0.1:8000"
    routing_mode: str = "local-only"
    local_model_provider: str = "transformers-smolvlm"
    local_model_id: str = "HuggingFaceTB/SmolVLM-500M-Instruct"
    local_model_base_url: str = ""
    local_model_timeout_seconds: float = 180.0
    online_model_enabled: bool = False
    online_model_provider: str = "openai-compatible"
    online_model_id: str = "gpt-4.1-mini"
    online_model_base_url: str = "https://api.openai.com/v1"
    online_model_api_key: str | None = None
    online_model_timeout_seconds: float = 120.0
    api_timeout_seconds: float = 180.0
    conversation_history_limit: int = 12
    state_file_path: str = ".cleo/state.json"
    argus_enabled: bool = True
    argus_base_url: str = "http://127.0.0.1:8010"
    argus_timeout_seconds: float = 60.0

    model_config = SettingsConfigDict(env_prefix="CLEO_", env_file=".env")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
