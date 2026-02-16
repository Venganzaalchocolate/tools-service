FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # fuerza CPU (evita intentos raros de GPU)
    CUDA_VISIBLE_DEVICES=-1 \
    # ruta estándar donde rembg busca el modelo
    U2NET_HOME=/root/.u2net

WORKDIR /app

# deps sistema: curl para bajar el modelo en build-time
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
  && rm -rf /var/lib/apt/lists/*

# 🔥 Descarga el modelo en build-time para evitar bajarlo al arrancar (y evitar picos RAM)
RUN mkdir -p /root/.u2net \
 && curl -L -o /root/.u2net/u2net.onnx \
    https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2net.onnx

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["sh", "-c", "python -m uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
