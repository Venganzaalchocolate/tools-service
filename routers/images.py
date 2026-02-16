# routes/image_routes.py  (o donde tengas tu router)
import os

# ⚠️ Capar threads ANTES de cargar sesiones/onnx
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

import io
import gc
import time
import zipfile
import logging
import traceback
import asyncio

from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from PIL import Image, ImageOps

from core.config import MAX_BYTES

from rembg import remove
from rembg.session_factory import new_session


router = APIRouter(prefix="/image", tags=["images"])

# Logs
DEBUG_LOGS = os.getenv("DEBUG_LOGS", "0") in ("1", "true", "True", "yes", "YES")
logger = logging.getLogger("tools-service.image")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)

# Render 0.5 CPU / 512MB: valores agresivos
MAX_SIDE = int(os.getenv("MAX_SIDE", "768"))           # 640/768 recomendado
MAX_PIXELS = int(os.getenv("MAX_PIXELS", "900000"))    # 0.5-0.9MP recomendado

# Modelo más ligero que u2net
REMBG_MODEL = os.getenv("REMBG_MODEL", "u2netp")
SESSION = new_session(REMBG_MODEL)

# Limitar concurrencia (0.5 CPU => 1)
REMBG_CONCURRENCY = int(os.getenv("REMBG_CONCURRENCY", "1"))
REMBG_SEMAPHORE = asyncio.Semaphore(REMBG_CONCURRENCY)


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


def _preprocess_before_rembg(inp: bytes) -> bytes:
    """
    ✅ Hace lo barato ANTES de rembg:
    - EXIF transpose (móvil)
    - downscale fuerte
    - convierte a RGB y lo guarda como JPEG (más ligero que PNG)
    """
    im = Image.open(io.BytesIO(inp))
    im = ImageOps.exif_transpose(im)

    w, h = im.size
    if (w * h) > MAX_PIXELS or max(w, h) > MAX_SIDE:
        im.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)

    buf = io.BytesIO()
    im.convert("RGB").save(buf, format="JPEG", quality=85, optimize=True)
    im.close()
    return buf.getvalue()


def _clean_alpha(img_rgba: Image.Image, cutoff: int = 8) -> Image.Image:
    """
    Quita halos: alpha muy bajo -> 0
    """
    r, g, b, a = img_rgba.split()
    a = a.point(lambda p: 0 if p < cutoff else p)
    return Image.merge("RGBA", (r, g, b, a))


def _crop_to_subject_rgba(
    img_rgba: Image.Image,
    padding_ratio: float = 0.18,
    alpha_threshold: int = 10
) -> Image.Image:
    """
    Recorta por bbox real (alpha > threshold)
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
    Cuadrado transparente + reencuadre (sube el sujeto un poco)
    """
    side = max(img_rgba.width, img_rgba.height)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))

    ox = (side - img_rgba.width) // 2
    oy = (side - img_rgba.height) // 2

    # sube el sujeto
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
        if DEBUG_LOGS:
            logger.info("====== [profile-bundle] START ======")
            logger.info("filename=%s content_type=%s", getattr(file, "filename", None), getattr(file, "content_type", None))

        # 1) leer bytes + validar
        inp = await _read_upload_bytes(file)

        # 2) preprocess ANTES de rembg (threadpool)
        inp = await run_in_threadpool(_preprocess_before_rembg, inp)
        if DEBUG_LOGS:
            logger.info("preprocess_ok bytes=%s", len(inp))

        # 3) rembg (caro) con sesión reutilizada + concurrencia limitada
        async with REMBG_SEMAPHORE:
            t_r0 = time.time()
            out_png = await run_in_threadpool(remove, inp, session=SESSION)

        if not out_png or len(out_png) < 100:
            raise HTTPException(status_code=500, detail="rembg devolvió salida vacía")

        if DEBUG_LOGS:
            logger.info("rembg_ok out_png_bytes=%s t=%ss", len(out_png), round(time.time() - t_r0, 3))

        # 4) PIL decode + limpieza halos + crop sujeto
        img = Image.open(io.BytesIO(out_png)).convert("RGBA")
        img = _clean_alpha(img, cutoff=8)
        img = _crop_to_subject_rgba(img, padding_ratio=0.18, alpha_threshold=10)

        # 5) square + resize
        img512 = _square_and_resize(img, size=512, y_bias=0.10)
        img92 = _square_and_resize(img, size=92, y_bias=0.14)

        # 6) bytes PNG
        png512 = _to_png_bytes(img512)
        png92 = _to_png_bytes(img92)

        # 7) ZIP
        zbuf = io.BytesIO()
        with zipfile.ZipFile(zbuf, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr("profile_512.png", png512)
            z.writestr("profile_92.png", png92)

        zip_bytes = zbuf.getvalue()

        if DEBUG_LOGS:
            logger.info("zip_ok zip_bytes=%s total=%ss", len(zip_bytes), round(time.time() - t0, 3))
            logger.info("====== [profile-bundle] END ======")

        return Response(content=zip_bytes, media_type="application/zip")

    except HTTPException as e:
        logger.warning("[profile-bundle] HTTPException %s: %s", e.status_code, e.detail)
        raise

    except Exception as e:
        logger.error("[profile-bundle] ERROR: %r", e)
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Error procesando la imagen")

    finally:
        # liberar memoria agresivo (clave en 512MB)
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
