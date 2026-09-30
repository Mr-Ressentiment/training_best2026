"""Runtime and environment configuration for OrderShield.

CRITICAL ARCHITECTURAL CONSTRAINTS & INVARIANTS:
1. NO AUTOMATIC REPLAY/FIXTURE FALLBACK VIA CONFIGURATION:
   Live intake (`/api/v1/orders/ingest`) MUST NEVER automatically switch or fall back
   to fixture or replay mode through any configuration flag or runtime error.
   Replay is permitted exclusively through dedicated `/api/v1/fixtures/...` routes.
   No configuration setting or flag exists to redirect live intake traffic to FixtureAIProvider.

2. NO AUTOMATIC PROVIDER FAILOVER OR SILENT SUBSTITUTION:
   Live AI inference strictly uses the provider specified by `LLM_PROVIDER` (default: "qwen").
   The application runtime does NOT perform dynamic or automatic failover between providers
   (e.g., Qwen to Gemini). Any intake failure or timeout produces an explicit diagnostic error.
   Switching providers requires explicit administrative configuration and service restart.
"""

from typing import Optional
import os


def _get_float_env(key: str, default: float) -> float:
    """Safely parse a float environment variable with fallback to default."""
    raw_val = os.getenv(key)
    if raw_val is None:
        return default
    try:
        return float(raw_val.strip())
    except (ValueError, TypeError):
        return default


class Settings:
    """Application runtime settings read from environment variables.

    Implemented using standard library `os` to avoid external dependencies
    (such as `pydantic-settings`).
    """

    def __init__(
        self,
        llm_provider: Optional[str] = None,
        llm_api_key: Optional[str] = None,
        database_url: Optional[str] = None,
        live_inference_timeout: Optional[float] = None,
        immediate_failure_timeout: Optional[float] = None,
    ) -> None:
        self.llm_provider: str = (
            llm_provider
            if llm_provider is not None
            else os.getenv("LLM_PROVIDER", "qwen")
        )
        self.llm_api_key: str = (
            llm_api_key
            if llm_api_key is not None
            else os.getenv("LLM_API_KEY", "")
        )
        self.database_url: str = (
            database_url
            if database_url is not None
            else os.getenv("DATABASE_URL", "sqlite:///ordershield.db")
        )
        self.live_inference_timeout: float = (
            live_inference_timeout
            if live_inference_timeout is not None
            else _get_float_env("LIVE_INFERENCE_TIMEOUT", 15.0)
        )
        self.immediate_failure_timeout: float = (
            immediate_failure_timeout
            if immediate_failure_timeout is not None
            else _get_float_env("IMMEDIATE_FAILURE_TIMEOUT", 5.0)
        )

    @property
    def LLM_PROVIDER(self) -> str:
        return self.llm_provider

    @property
    def LLM_API_KEY(self) -> str:
        return self.llm_api_key

    @property
    def DATABASE_URL(self) -> str:
        return self.database_url

    @property
    def LIVE_INFERENCE_TIMEOUT(self) -> float:
        return self.live_inference_timeout

    @property
    def IMMEDIATE_FAILURE_TIMEOUT(self) -> float:
        return self.immediate_failure_timeout


# Global singleton settings instance
settings = Settings()
