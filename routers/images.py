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
import asyncio
from starlette.concurrency import run_in_threadpool

router = APIRouter(prefix="/image", tags=["images"])

DEBUG_LOGS = os.getenv("DEBUG_LOGS", "0") in ("1", "true", "True", "yes", "YES")
logger = logging.getLogger("tools-service.image")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)

# Ajustes para 512MB
MAX_SIDE = int(os.getenv("MAX_SIDE", "1024"))          # 768/1024 recomendado
MAX_PIXELS = int(os.getenv("MAX_PIXELS", "1500000"))   # 1.5MP recomendado
JPEG_QUALITY = int(os.getenv("JPEG_QUALITY", "85"))    # 80-88 suele ir bien

# 🔥 clave en 512MB: evita concurrencia
SEM = asyncio.Semaphore(int(os.getenv("MAX_CONCURRENCY", "1")))


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


def _downscale_before_rembg(inp: bytes) -> bytes:
    """
    Reduce resolución ANTES de rembg para ahorrar RAM.
    Exporta a JPEG (más ligero de codificar que PNG en muchos casos).
    """
    im = Image.open(io.BytesIO(inp))
    im = ImageOps.exif_transpose(im)  # respeta orientación móvil
    w, h = im.size

    if (w * h) > MAX_PIXELS or max(w, h) > MAX_SIDE:
        im.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)

    buf = io.BytesIO()
    im.convert("RGB").save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    im.close()
    return buf.getvalue()


def _clean_alpha(img_rgba: Image.Image, cutoff: int = 8) -> Image.Image:
    r, g, b, a = img_rgba.split()
    a = a.point(lambda p: 0 if p < cutoff else p)
    return Image.merge("RGBA", (r, g, b, a))


def _crop_to_subject_rgba(img_rgba: Image.Image, padding_ratio: float = 0.18, alpha_threshold: int = 10) -> Image.Image:
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
    side = max(img_rgba.width, img_rgba.height)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))

    ox = (side - img_rgba.width) // 2
    oy = (side - img_rgba.height) // 2

    # sube el sujeto (mejor avatar)
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
    async with SEM:
        t0 = time.time()
        inp = out_png = None
        img = img512 = img92 = None

        try:
            inp = await _read_upload_bytes(file)

            # 🔥 clave: reducir antes de rembg (en threadpool)
            inp = await run_in_threadpool(_downscale_before_rembg, inp)

            if DEBUG_LOGS:
                logger.info("downscale_ok bytes=%s", len(inp))

            # rembg (pesado) en threadpool
            out_png = await run_in_threadpool(remove, inp)
            if not out_png or len(out_png) < 100:
                raise HTTPException(status_code=500, detail="rembg devolvió salida vacía")

            img = Image.open(io.BytesIO(out_png)).convert("RGBA")

            # limpiar halos + crop
            img = _clean_alpha(img, cutoff=8)
            img = _crop_to_subject_rgba(img, padding_ratio=0.18, alpha_threshold=10)

            # sizes
            img512 = _square_and_resize(img, size=512, y_bias=0.10)
            img92 = _square_and_resize(img, size=92, y_bias=0.14)

            png512 = _to_png_bytes(img512)
            png92 = _to_png_bytes(img92)

            zbuf = io.BytesIO()
            with zipfile.ZipFile(zbuf, "w", compression=zipfile.ZIP_DEFLATED) as z:
                z.writestr("profile_512.png", png512)
                z.writestr("profile_92.png", png92)

            zip_bytes = zbuf.getvalue()

            if DEBUG_LOGS:
                logger.info("zip_ok bytes=%s total=%ss", len(zip_bytes), round(time.time() - t0, 3))

            return Response(
                content=zip_bytes,
                media_type="application/zip",
                headers={"Cache-Control": "no-store"}
            )

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
