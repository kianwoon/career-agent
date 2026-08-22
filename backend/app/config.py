"""Application configuration loaded from environment variables."""

from functools import lru_cache
from typing import Annotated

from pydantic import BeforeValidator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _parse_list(v):
    """Accept both JSON arrays and comma-separated strings for list fields."""
    if isinstance(v, str):
        v = v.strip()
        if v.startswith("["):
            import json as _json

            try:
                return _json.loads(v)
            except Exception:
                pass
        return [x.strip() for x in v.split(",") if x.strip()]
    return v


# List fields that may arrive as JSON or comma-separated from env hosts.
# NoDecode prevents pydantic-settings from force-JSON-decoding list fields.
StrList = Annotated[list[str], NoDecode, BeforeValidator(_parse_list)]


class Settings(BaseSettings):
    """Runtime settings. Override via environment variables or .env file."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Career Agent API"
    debug: bool = False

    # Postgres
    database_url: str = "postgresql+asyncpg://career:career@localhost:5432/career_agent"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Qdrant
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "career_documents"

    # Steel browser service
    steel_api_url: str = "http://localhost:3000"
    steel_api_key: str = ""

    # Browser session capture/replay (encrypted cookie storage).
    # 32-byte (64 hex chars) AES-256 key for encrypting stored sessions.
    # Set via env: SESSION_ENCRYPTION_KEY. Derive if empty (dev only, warn).
    session_encryption_key: str = ""
    # Domain scoping for captured sessions (comma-separated).
    session_domains: StrList = ["linkedin.com", "www.linkedin.com"]

    # LLM (optional; not required for Phase 1 deterministic path)
    openai_api_key: str = ""
    llm_model: str = "gpt-4o-mini"

    # Z.AI GLM coding-plan LLM (optional enhancement layer).
    # The coding plan subscription requires requests to be wrapped with AI
    # coding-tool headers so the provider recognizes the plan.
    llm_enabled: bool = False
    llm_base_url: str = "https://api.z.ai/api/anthropic/v1/messages"
    llm_api_key: str = ""  # set via env: LLM_API_KEY
    llm_model_name: str = "GLM-5.3"
    llm_max_tokens: int = 2048
    llm_timeout_s: int = 60
    llm_session_name: str = "career-agent"

    # Approval policy
    approvals_enabled: bool = True

    # Polite pacing (anti-robot detection avoidance via reasonable usage).
    # Randomized delays between browser actions make traffic look human.
    pacing_min_delay_ms: int = 1500
    pacing_max_delay_ms: int = 4000
    pacing_max_pages_per_min: int = 12
    pacing_throttle_window_s: int = 60
    pacing_min_search_gap_s: int = 45
    pacing_circuit_breaker_s: int = 300  # backoff after a challenge/block

    # CORS for the Next.js dev server
    cors_origins: StrList = ["http://localhost:3000"]

    # External API security.
    # Comma-separated list of valid API keys, e.g. "key1,key2". Each key can
    # have a per-key rate limit suffix: "key1:60" = 60 requests/minute.
    # Leave empty to disable auth (local dev only — NOT for external use).
    api_keys: str = ""
    # Default per-key rate limit (requests per minute) when no suffix given.
    api_rate_limit_per_min: int = 30
    # Whether /health and /docs stay open without auth.
    api_public_paths: StrList = ["/api/v1/health", "/docs", "/openapi.json"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
