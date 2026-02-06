from fastapi import FastAPI
from routers.health import router as health_router
from routers.images import router as images_router

app = FastAPI(title="Tools Service", version="1.0.0")
app.include_router(health_router)
app.include_router(images_router)
