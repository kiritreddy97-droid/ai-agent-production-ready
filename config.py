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
