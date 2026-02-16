from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import Response
from core.config import MAX_BYTES
from rembg import remove
from PIL import Image, ImageOps
import io
import zipfile
import traceback
import time
import os
import logging
import gc
from starlette.concurrency import run_in_threadpool

router = APIRouter(prefix="/image", tags=["images"])

DEBUG_LOGS = os.getenv("DEBUG_LOGS", "0") in ("1", "true", "True", "yes", "YES")
logger = logging.getLogger("tools-service.image")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)

# Ajustes para instancias pequeñas (Render 512MB)
MAX_SIDE = int(os.getenv("MAX_SIDE", "1024"))           # lado mayor antes de rembg
MAX_PIXELS = int(os.getenv("MAX_PIXELS", "1500000"))    # 1.5MP


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


def _square_crop_and_downscale_before_rembg(
    inp: bytes,
    max_side: int = MAX_SIDE,
    max_pixels: int = MAX_PIXELS,
    y_bias: float = 0.12,     # 0 centro; 0.10-0.18 sube
) -> bytes:
    """
    1) Respeta EXIF
    2) Recorta a cuadrado ANTES de rembg (mejor encuadre)
    3) Reduce resolución fuerte para ahorrar RAM
    4) Devuelve JPEG (menos peso/RAM que PNG)
    """
    im = Image.open(io.BytesIO(inp))
    im = ImageOps.exif_transpose(im)

    w, h = im.size
    side = min(w, h)

    cx = w // 2
    cy = int(h // 2 - side * y_bias)

    left = cx - side // 2
    top = cy - side // 2

    # clamp para no salirnos
    if left < 0:
        left = 0
    if top < 0:
        top = 0
    if left + side > w:
        left = w - side
    if top + side > h:
        top = h - side

    im = im.crop((left, top, left + side, top + side))

    # downscale por píxeles y por lado
    w2, h2 = im.size
    if (w2 * h2) > max_pixels:
        # escala para cumplir max_pixels
        import math
        scale = math.sqrt(max_pixels / float(w2 * h2))
        nw = max(1, int(w2 * scale))
        nh = max(1, int(h2 * scale))
        im = im.resize((nw, nh), Image.LANCZOS)

    if max(im.size) > max_side:
        im = im.resize((max_side, max_side), Image.LANCZOS)

    buf = io.BytesIO()
    im.convert("RGB").save(buf, format="JPEG", quality=85, optimize=True)
    im.close()
    return buf.getvalue()


def _clean_alpha(img_rgba: Image.Image, cutoff: int = 8) -> Image.Image:
    """
    Quita halos/suciedad: todo alpha muy bajo -> 0
    """
    r, g, b, a = img_rgba.split()
    a = a.point(lambda p: 0 if p < cutoff else p)
    return Image.merge("RGBA", (r, g, b, a))


def _crop_to_subject_rgba(img_rgba: Image.Image, padding_ratio: float = 0.18, alpha_threshold: int = 10) -> Image.Image:
    """
    Recorta al sujeto usando bbox con umbral (ignora halos de alpha bajo).
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


def _square_and_resize(img_rgba: Image.Image, size: int, y_bias: float = 0.12) -> Image.Image:
    """
    Canvas cuadrado transparente + reencuadre.
    y_bias sube el sujeto para evitar aire abajo.
    """
    side = max(img_rgba.width, img_rgba.height)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))

    ox = (side - img_rgba.width) // 2
    oy = (side - img_rgba.height) // 2

    oy = int(oy - side * y_bias)
    oy = max(min(oy, side - img_rgba.height), 0)

    canvas.paste(img_rgba, (ox, oy), img_rgba)
    return canvas.resize((size, size), Image.LANCZOS)


def _to_png_bytes(img_rgba: Image.Image) -> bytes:
    buf = io.BytesIO()
    img_rgba.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


@router.post("/profile-bundle")
async def profile_bundle(file: UploadFile = File(...)):
    t0 = time.time()
    inp = out_png = None
    img = img512 = img92 = None

    try:
        # 1) leer bytes + validar
        inp = await _read_upload_bytes(file)

        # 2) 🔥 cuadrar + downscale ANTES de rembg (clave para encuadre + RAM)
        inp = await run_in_threadpool(_square_crop_and_downscale_before_rembg, inp)

        if DEBUG_LOGS:
            logger.info("pre_rembg_square_ok bytes=%s", len(inp))

        # 3) rembg (pesado) -> threadpool
        out_png = await run_in_threadpool(remove, inp)
        if not out_png or len(out_png) < 100:
            raise HTTPException(status_code=500, detail="rembg devolvió salida vacía")

        # 4) PIL decode
        img = Image.open(io.BytesIO(out_png)).convert("RGBA")

        # 5) limpiar halos + crop al sujeto
        img = _clean_alpha(img, cutoff=8)
        img = _crop_to_subject_rgba(img, padding_ratio=0.18, alpha_threshold=10)

        # 6) sizes finales
        img512 = _square_and_resize(img, size=512, y_bias=0.10)
        img92 = _square_and_resize(img, size=92, y_bias=0.14)

        png512 = _to_png_bytes(img512)
        png92 = _to_png_bytes(img92)

        # 7) zip
        zbuf = io.BytesIO()
        with zipfile.ZipFile(zbuf, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr("profile_512.png", png512)
            z.writestr("profile_92.png", png92)

        zip_bytes = zbuf.getvalue()

        if DEBUG_LOGS:
            logger.info("zip_ok bytes=%s total=%ss", len(zip_bytes), round(time.time() - t0, 3))

        return Response(content=zip_bytes, media_type="application/zip")

    except HTTPException:
        raise

    except Exception as e:
        logger.error("[profile-bundle] ERROR: %r", e)
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Error procesando la imagen")

    finally:
        # liberar memoria agresivo (importante en 512MB)
        try:
            if img: img.close()
            if img512: img512.close()
            if img92: img92.close()
        except Exception:
            pass
        inp = None
        out_png = None
        img = None
        img512 = None
        img92 = None
        gc.collect()
