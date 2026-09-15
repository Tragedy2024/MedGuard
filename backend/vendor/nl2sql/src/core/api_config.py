import os
from pathlib import Path
import httpx
from openai import OpenAI

# ---- Auto-load .env file (if present) ----
# Searches from project root upward. Finds the first .env file.
def _load_dotenv():
    """Simple .env loader — no external dependencies needed."""
    candidate = Path(__file__).resolve()
    for _ in range(6):  # search up to 6 levels from vendor/MAC-SQL/core/
        candidate = candidate.parent
        env_file = candidate / ".env"
        if env_file.exists():
            with open(env_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#') or '=' not in line:
                        continue
                    key, _, val = line.partition('=')
                    key, val = key.strip(), val.strip()
                    if key and val and key not in os.environ:
                        os.environ[key] = val
            return

_load_dotenv()

# ---- Configuration ----
# Set via environment variable OR .env file in project root.
OPENAI_API_BASE = os.getenv("OPENAI_API_BASE", "https://api.deepseek.com/v1")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
MODEL_NAME = os.getenv("MODEL_NAME", "deepseek-v4-pro")

# Initialize OpenAI client.
# trust_env=False: bypass the Windows system proxy (e.g. Clash on
# 127.0.0.1:7897). The local proxy client becomes unstable under parallel
# LLM load and silently kills API connections; api.deepseek.com is
# directly reachable, so we connect to it without the proxy.
client = OpenAI(
    base_url=OPENAI_API_BASE,
    api_key=OPENAI_API_KEY,
    http_client=httpx.Client(trust_env=False),
)
