# core/security.py
from fastapi import Header, HTTPException
from core.config import API_KEY_BACK, DEBUG

def require_key(x_api_key: str | None = Header(default=None)):
  if DEBUG:
    print("[auth] API_KEY_BACK len:", len(API_KEY_BACK or ""))
    print("[auth] X-Api-Key received:", x_api_key)

  if API_KEY_BACK and x_api_key != API_KEY_BACK:
    raise HTTPException(status_code=401, detail="Unauthorized")
  return True
