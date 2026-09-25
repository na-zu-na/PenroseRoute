from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    dispatch_intent_provider: Literal["rules", "bedrock"] = "rules"
    dispatch_api_tokens: SecretStr | None = None
    dispatch_context_secret: SecretStr | None = None
    business_timezone: str = "Asia/Singapore"
    agent_explanation_provider: Literal["template", "bedrock"] = "template"
    recovery_orchestration_mode: Literal["deterministic", "agent"] = "deterministic"
    bedrock_model_id: str | None = None
    bedrock_endpoint_url: str | None = None
    agent_connect_timeout_seconds: float = Field(default=3, gt=0, le=30)
    agent_read_timeout_seconds: float = Field(default=10, gt=0, le=60)
    aws_region: str = "ap-southeast-1"

    app_name: str = "PenroseRoute API"
    app_env: str = "development"
    frontend_origins: str = (
        "http://localhost:5173,http://127.0.0.1:5173,"
        "http://localhost:3000,http://127.0.0.1:3000"
    )
    database_url: str = (
        "postgresql+psycopg://postgres:postgres@localhost:5432/penrose_route"
    )
    at_risk_threshold_seconds: int = Field(default=900, ge=0)

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
