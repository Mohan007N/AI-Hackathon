"""
Configuration module for Safe Web Interactive Agent backend.
Reads all settings from environment variables via python-dotenv.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file from backend directory or project root
_env_path = Path(__file__).parent.parent / ".env"
if _env_path.exists():
    load_dotenv(dotenv_path=_env_path)
else:
    load_dotenv()


class Settings:
    # ── Organizer Portal ──────────────────────────────────────────────────────
    PORTAL_URL: str = os.getenv("PORTAL_URL", "")
    TEAM_CODE: str = os.getenv("TEAM_CODE", "")

    # ── LLM via OpenRouter ────────────────────────────────────────────────────
    OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
    OPENROUTER_MODEL: str = os.getenv(
        "OPENROUTER_MODEL", "openai/gpt-4o-mini"
    )
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"

    # ── Frontend ──────────────────────────────────────────────────────────────
    FRONTEND_URL: str = os.getenv("FRONTEND_URL", "http://localhost:5173")

    # ── Logging ───────────────────────────────────────────────────────────────
    LOG_DIRECTORY: str = os.getenv(
        "LOG_DIRECTORY",
        str(Path(__file__).parent.parent / "logs"),
    )

    # ── Browser ───────────────────────────────────────────────────────────────
    BROWSER_HEADLESS: bool = os.getenv("BROWSER_HEADLESS", "true").lower() in (
        "true", "1", "yes"
    )
    BROWSER_TIMEOUT_MS: int = int(os.getenv("BROWSER_TIMEOUT_MS", "30000"))

    # ── HTTP Client ───────────────────────────────────────────────────────────
    HTTP_TIMEOUT: int = int(os.getenv("HTTP_TIMEOUT", "30"))

    # ── App ───────────────────────────────────────────────────────────────────
    APP_ENV: str = os.getenv("APP_ENV", "development")
    DEBUG: bool = os.getenv("DEBUG", "false").lower() in ("true", "1", "yes")

    def validate(self) -> list[str]:
        """Return list of validation warnings for missing critical config."""
        warnings: list[str] = []
        if not self.PORTAL_URL:
            warnings.append("PORTAL_URL is not set")
        if not self.TEAM_CODE:
            warnings.append("TEAM_CODE is not set")
        if not self.OPENROUTER_API_KEY:
            warnings.append("OPENROUTER_API_KEY is not set")
        return warnings


settings = Settings()

