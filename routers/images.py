from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import Response
from core.config import MAX_BYTES
from rembg import remove
from PIL import Image
import io
import zipfile
import traceback
import time
import os
import logging
from starlette.concurrency import run_in_threadpool

router = APIRouter(prefix="/image", tags=["images"])

DEBUG_LOGS = os.getenv("DEBUG_LOGS", "0") in ("1", "true", "True", "yes", "YES")

logger = logging.getLogger("tools-service.image")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)


async def _read_upload_bytes(upload: UploadFile) -> bytes:
    if not upload:
        raise HTTPException(status_code=400, detail="Missing file")

    ct = getattr(upload, "content_type", None)
    if ct not in ("image/jpeg", "image/png", "image/webp"):
        raise HTTPException(status_code=400, detail=f"Formato no permitido: {ct}. Usa JPG/PNG/WEBP")

    data = await upload.read()

    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(data) > MAX_BYTES:
        raise HTTPException(status_code=413, detail=f"Imagen demasiado grande (máx {MAX_BYTES} bytes)")
    return data


def _pre_square_crop_rgb(img: Image.Image, y_bias: float = 0.08) -> Image.Image:
    """
    Recorta ANTES de rembg a cuadrado (sin transparencia).
    y_bias > 0 sube un poco el encuadre para que no corte cabeza.
    """
    img = img.convert("RGB")
    w, h = img.size
    side = min(w, h)

    # centro base
    cx = w // 2
    cy = h // 2

    # sube el centro un pelín (mejor retratos)
    cy = int(cy - h * y_bias)

    left = max(0, cx - side // 2)
    top = max(0, cy - side // 2)

    # ajusta para no salirte
    if left + side > w:
        left = w - side
    if top + side > h:
        top = h - side

    return img.crop((left, top, left + side, top + side))


def _clean_alpha(img_rgba: Image.Image, cutoff: int = 24) -> Image.Image:
    """
    Elimina alpha muy bajo (halos) -> 0.
    cutoff más alto = bbox más "apretado".
    """
    r, g, b, a = img_rgba.split()
    a = a.point(lambda p: 0 if p < cutoff else p)
    return Image.merge("RGBA", (r, g, b, a))


def _crop_to_subject_rgba(img_rgba: Image.Image, padding_ratio: float = 0.10, alpha_threshold: int = 40) -> Image.Image:
    """
    bbox del sujeto usando alpha_threshold alto para ignorar halos.
    """
    alpha = img_rgba.split()[-1]
    mask = alpha.point(lambda p: 255 if p > alpha_threshold else 0)
    bbox = mask.getbbox()
    if not bbox:
        return img_rgba

    x0, y0, x1, y1 = bbox
    w = x1 - x0
    h = y1 - y0
    pad = int(max(w, h) * padding_ratio)

    x0 = max(0, x0 - pad)
    y0 = max(0, y0 - pad)
    x1 = min(img_rgba.width, x1 + pad)
    y1 = min(img_rgba.height, y1 + pad)
    return img_rgba.crop((x0, y0, x1, y1))


def _square_and_resize(img_rgba: Image.Image, size: int) -> Image.Image:
    """
    Cuadrado + resize SIN y_bias (para evitar el hueco abajo).
    """
    side = max(img_rgba.width, img_rgba.height)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    ox = (side - img_rgba.width) // 2
    oy = (side - img_rgba.height) // 2
    canvas.paste(img_rgba, (ox, oy), img_rgba)
    return canvas.resize((size, size), Image.LANCZOS)


def _to_png_bytes(img_rgba: Image.Image) -> bytes:
    buf = io.BytesIO()
    img_rgba.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


@router.post("/profile-bundle")
async def profile_bundle(file: UploadFile = File(...)):
    """
    ZIP con:
      - profile_512.png
      - profile_96.png   (compat con tu back Node)
    """
    t0 = time.time()

    try:
        inp = await _read_upload_bytes(file)

        # A) pre-crop cuadrado ANTES de rembg
        orig = Image.open(io.BytesIO(inp))
        pre = _pre_square_crop_rgb(orig, y_bias=0.08)

        pre_buf = io.BytesIO()
        pre.save(pre_buf, format="PNG")  # PNG para pasar a rembg consistente
        pre_bytes = pre_buf.getvalue()

        # B) rembg en threadpool (mejor calidad con alpha_matting)
        # (si ves que tarda mucho, quita alpha_matting=True)
        out_png = await run_in_threadpool(
            remove,
            pre_bytes,
            alpha_matting=True,
            alpha_matting_foreground_threshold=240,
            alpha_matting_background_threshold=10,
            alpha_matting_erode_size=10,
        )
        if not out_png or len(out_png) < 100:
            raise HTTPException(status_code=500, detail="rembg devolvió salida vacía")

        # C) RGBA
        img = Image.open(io.BytesIO(out_png)).convert("RGBA")

        # D) limpia halos + recorta bien al sujeto
        img = _clean_alpha(img, cutoff=24)
        img = _crop_to_subject_rgba(img, padding_ratio=0.10, alpha_threshold=40)

        # E) cuadrados finales
        img512 = _square_and_resize(img, 512)
        img96 = _square_and_resize(img, 96)

        # F) zip
        zbuf = io.BytesIO()
        with zipfile.ZipFile(zbuf, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr("profile_512.png", _to_png_bytes(img512))
            z.writestr("profile_96.png", _to_png_bytes(img96))

        return Response(content=zbuf.getvalue(), media_type="application/zip")

    except HTTPException:
        raise
    except Exception as e:
        logger.error("[profile-bundle] ERROR: %r", e)
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Error procesando la imagen")
