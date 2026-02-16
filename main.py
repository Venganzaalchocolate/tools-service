# main.py
from fastapi import FastAPI, Depends
from core.security import require_key
from core.config import DEBUG
from routers.health import router as health_router
from routers.images import router as images_router

app = FastAPI(
  title="Tools Service",
  version="1.0.0",
  docs_url="/docs" if DEBUG else None,
  redoc_url=None,
  openapi_url="/openapi.json" if DEBUG else None,
  dependencies=[Depends(require_key)],
)

app.include_router(health_router)
app.include_router(images_router)

# === [DEBUG_ROUTES] START ===
from fastapi.routing import APIRoute

@app.on_event("startup")
def _debug_print_routes():
  if not DEBUG:
    return
  print("\n=== ROUTES ===")
  for r in app.routes:
    if isinstance(r, APIRoute):
      print("[route]", sorted(list(r.methods)), r.path)
  print("=== /ROUTES ===\n")
# === [DEBUG_ROUTES] END ===
