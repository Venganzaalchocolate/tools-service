from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from fastapi.responses import Response
from core.config import MAX_BYTES, DEBUG, API_KEY
from core.security import require_key
from services.bg_remove import remove_background_bytes


router = APIRouter(prefix="/image", tags=["image"])

@router.post("/remove-background", dependencies=[Depends(require_key)])
async def remove_background(image_file: UploadFile = File(...)):
    if image_file.content_type not in ("image/png", "image/jpeg", "image/webp"):
        raise HTTPException(status_code=400, detail="Formato no permitido (png/jpg/webp)")

    inp = await image_file.read()
    if not inp:
        raise HTTPException(status_code=400, detail="Archivo vacío")
    if len(inp) > MAX_BYTES:
        raise HTTPException(status_code=400, detail=f"Imagen demasiado grande (máx {MAX_BYTES} bytes)")

    # === [PRECHECK_LOGS] START ===
    if DEBUG:
        print("[tools-service] precheck -> url: /image/remove-background")
        print("[tools-service] precheck -> apiKey present:", bool(API_KEY))
        print("[tools-service] precheck -> filename:", image_file.filename)
        print("[tools-service] precheck -> content_type:", image_file.content_type)
        print("[tools-service] precheck -> in_bytes:", len(inp))
    # === [PRECHECK_LOGS] END ===

    try:
        out, ms = remove_background_bytes(inp)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"rembg error: {str(e)}")

    if DEBUG:
        print("[tools-service] done -> out_bytes:", len(out), "ms:", ms)

    return Response(content=out, media_type="image/png")
