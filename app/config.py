import os
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MODEL_LABS_KEY = os.getenv("MODEL_LABS_KEY", "")
ADMIN_KEY = os.getenv("ADMIN_KEY", "")
SEED_USERNAME = os.getenv("SEED_USERNAME", "neiv")
HF_TOKEN = os.getenv("HF_TOKEN", "")
HF_DATASET_REPO = os.getenv("HF_DATASET_REPO", "")
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
SUPABASE_STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "roomai-media").strip()
try:
    STORAGE_SIGNED_URL_TTL = max(60, int(os.getenv("STORAGE_SIGNED_URL_TTL", "3600")))
except ValueError:
    STORAGE_SIGNED_URL_TTL = 3600

USE_MODEL_LABS = True

MAX_SIZE_BYTES = 4 * 1024 * 1024
MAX_DIMENSION = 1024


def _normalize_database_url(url: str) -> str:
    """Ensure password special chars are URL-encoded and SSL is required."""
    if not url:
        return ""
    # Already has encoded password if % present in userinfo
    try:
        # Force sslmode=require for Supabase
        if "sslmode=" not in url:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}sslmode=require"
        return url
    except Exception:
        return url


DATABASE_URL = _normalize_database_url(os.getenv("DATABASE_URL", "").strip())
