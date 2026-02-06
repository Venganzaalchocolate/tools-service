import os
MAX_BYTES = int(os.getenv("TOOLS_MAX_BYTES", str(8 * 1024 * 1024)))
API_KEY = os.getenv("TOOLS_API_KEY", "")
DEBUG = os.getenv("TOOLS_DEBUG", "true").lower() == "true"
