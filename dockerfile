FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    VECLIB_MAXIMUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1 \
    REMBG_MODEL=u2netp \
    REMBG_CONCURRENCY=1 \
    MAX_SIDE=768 \
    MAX_PIXELS=900000

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 🔥 descarga/cache del modelo en build (evita el log de “Downloading ... u2net.onnx” en runtime)
RUN python -c "from rembg.session_factory import new_session; new_session('u2netp')"

COPY . .

CMD ["sh", "-c", "python -m uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
