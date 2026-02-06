# Tools Service (FastAPI) — Microservicios internos (solo accesibles por el backend)

Microservicio en **Python + FastAPI** para agrupar herramientas internas (p. ej. quitar fondo a imágenes) **NO expuestas públicamente**.  
La idea es que **solo tu backend Node** pueda consumir estos endpoints mediante una **API Key** enviada en cabecera.

---

## 🔒 Seguridad: API Key (solo backend)

Este servicio acepta una cabecera:

- `X-Api-Key: <TU_TOOLS_API_KEY>`

La key se guarda en `.env` (variable de entorno) y el servicio la valida en cada endpoint protegido.  
**Si la API key no coincide → 401 Unauthorized.**

> Recomendación de despliegue: que el servicio escuche solo en red interna / localhost y que el proxy sea tu backend.

---

## 📁 Estructura del proyecto

```
tools-service/
  main.py
  core/
    __init__.py
    config.py
    security.py
  routers/
    __init__.py
    health.py
    images.py
  services/
    __init__.py
    bg_remove.py
  requirements.txt
  .env.example
  .gitignore
  README.md
```

---

## ⚙️ Variables de entorno (.env)

Crea un archivo `.env` en la raíz del proyecto (NO se sube a GitHub):

**.env**
```env
TOOLS_API_KEY=pon_aqui_una_key_larga_y_random
TOOLS_DEBUG=true
TOOLS_MAX_BYTES=8388608
```

Ejemplo de key segura (puedes generar una):
- Windows PowerShell: `python -c "import secrets; print(secrets.token_hex(32))"`

---

## 🧪 Probar localmente

### 1) Crear y activar entorno virtual

**Windows (CMD)**
```bat
python -m venv .venv
.\.venv\Scripts\activate
```

### 2) Instalar dependencias

```bat
python -m pip install -r requirements.txt
```

### 3) Arrancar el servidor (dev)

```bat
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

- Docs: `http://localhost:8000/docs`
- Health: `http://localhost:8000/health`

---

## ✅ Cómo debe llamar tu backend Node (ejemplo)

Tu backend Node debe enviar la cabecera `X-Api-Key`.

Ejemplo (Node 18+):

```js
const res = await fetch("http://127.0.0.1:8000/image/remove-background", {
  method: "POST",
  headers: { "X-Api-Key": process.env.TOOLS_API_KEY },
  body: formData,
});
```

> En el navegador NO pongas esa key (no debe salir del backend).

---

## 🧷 Ejemplo con curl (para test rápido)

```bash
curl -X POST "http://localhost:8000/image/remove-background" \
  -H "X-Api-Key: TU_TOOLS_API_KEY" \
  -F "image_file=@./foto.jpg" \
  --output out.png
```

---

## 🧠 Nota importante de despliegue

Si esto es solo para tu backend, lo ideal es:
- correr el microservicio en la misma máquina/red que Node
- restringir firewall / docker network / reverse proxy
- y exigir `X-Api-Key` siempre

---
"# tools-service" 
