import os
from dotenv import load_dotenv

load_dotenv()

MAX_BYTES = int(os.getenv("MAX_BYTES", str(8 * 1024 * 1024)))
API_KEY_BACK = os.getenv("API_KEY_BACK", "")
DEBUG = os.getenv("DEBUG", "true").lower() == "true"
