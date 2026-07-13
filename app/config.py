import os
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MODEL_LABS_KEY = os.getenv("MODEL_LABS_KEY", "")
ADMIN_KEY = os.getenv("ADMIN_KEY", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")
SEED_USERNAME = os.getenv("SEED_USERNAME", "neiv")
HF_TOKEN = os.getenv("HF_TOKEN", "")
HF_DATASET_REPO = os.getenv("HF_DATASET_REPO", "")

USE_MODEL_LABS = True

MAX_SIZE_BYTES = 4 * 1024 * 1024
MAX_DIMENSION = 1024
