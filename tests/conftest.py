import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Keep tests hermetic: never hit the real API, log to a temp-friendly file.
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ.setdefault("LOG_FILE", str(ROOT / ".pytest_cache" / "agent-test.log"))
(ROOT / ".pytest_cache").mkdir(exist_ok=True)
