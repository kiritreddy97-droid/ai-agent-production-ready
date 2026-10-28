"""config.py -- Settings for ai-agent-production-ready. Author: Kirit Reddy Daida."""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=False)

class Settings:
    OPENAI_API_KEY: str  = os.getenv("OPENAI_API_KEY", "")
    MODEL: str           = os.getenv("OPENAI_MODEL", "gpt-4o")
    MAX_TOKENS: int      = int(os.getenv("MAX_TOKENS", "4096"))
    TEMPERATURE: float   = float(os.getenv("TEMPERATURE", "0.2"))
    MAX_ITERATIONS: int  = int(os.getenv("MAX_ITERATIONS", "10"))
    MAX_RETRIES: int     = int(os.getenv("MAX_RETRIES", "3"))
    API_HOST: str    = os.getenv("API_HOST", "0.0.0.0")
    API_PORT: int    = int(os.getenv("API_PORT", "8000"))
    API_WORKERS: int = int(os.getenv("API_WORKERS", "1"))
    API_RELOAD: bool = os.getenv("API_RELOAD", "false").lower() == "true"
    API_KEY: str     = os.getenv("API_KEY", "")
    WEATHER_API_KEY: str = os.getenv("WEATHER_API_KEY", "")
    SERPER_API_KEY: str  = os.getenv("SERPER_API_KEY", "")
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()
    LOG_FILE: str  = os.getenv("LOG_FILE", "agent.log")
    REDIS_URL: str   = os.getenv("REDIS_URL", "")
    SESSION_TTL: int = int(os.getenv("SESSION_TTL", "3600"))
    TASK_WORKERS: int = int(os.getenv("TASK_WORKERS", "2"))
    TASKS_DB: str     = os.getenv("TASKS_DB", "tasks.db")
    EVENT_BUFFER: int = int(os.getenv("EVENT_BUFFER", "500"))

    # LinkedIn autopilot (see campaign.py)
    GITHUB_USERNAME: str = os.getenv("GITHUB_USERNAME", "")
    GITHUB_TOKEN: str    = os.getenv("GITHUB_TOKEN", "")
    GITHUB_EXCLUDE: str  = os.getenv("GITHUB_EXCLUDE", "")
    ZENODO_ORCID: str    = os.getenv("ZENODO_ORCID", "")
    ZENODO_AUTHOR: str   = os.getenv("ZENODO_AUTHOR", "")
    LINKEDIN_CLIENT_ID: str     = os.getenv("LINKEDIN_CLIENT_ID", "")
    LINKEDIN_CLIENT_SECRET: str = os.getenv("LINKEDIN_CLIENT_SECRET", "")
    LINKEDIN_REDIRECT_URI: str  = os.getenv("LINKEDIN_REDIRECT_URI",
                                            "http://localhost:8000/linkedin/callback")
    LINKEDIN_API_VERSION: str   = os.getenv("LINKEDIN_API_VERSION", "")
    LINKEDIN_POST_DAYS: str     = os.getenv("LINKEDIN_POST_DAYS", "mon,thu")
    LINKEDIN_POST_TIME: str     = os.getenv("LINKEDIN_POST_TIME", "09:00")
    LINKEDIN_TIMEZONE: str      = os.getenv("LINKEDIN_TIMEZONE", "UTC")
    LINKEDIN_AUTO_PUBLISH: bool = os.getenv("LINKEDIN_AUTO_PUBLISH", "false").lower() == "true"
    LINKEDIN_DRAFT_AHEAD_HOURS: int = int(os.getenv("LINKEDIN_DRAFT_AHEAD_HOURS", "48"))

    def validate(self):
        if not self.OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY not set. Copy .env.example to .env")

    def __repr__(self):
        k = self.OPENAI_API_KEY[:8]+"..." if self.OPENAI_API_KEY else "(not set)"
        return f"Settings(model={self.MODEL}, port={self.API_PORT}, key={k})"

settings = Settings()

if __name__ == "__main__":
    print(repr(settings))
    print("OpenAI key:", "SET" if settings.OPENAI_API_KEY else "MISSING")
