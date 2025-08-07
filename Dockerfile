# syntax=docker/dockerfile:1
# ---------- 1) Imagen base ----------
FROM python:3.12-slim AS base

# Evita escribir pyc, muestra logs en tiempo real
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Dependencias de sistema mínimas (tzdata: zoneinfo necesita tablas)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ---------- 2) Librerías ----------
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---------- 3) Código ----------
COPY . .

# Carpeta de logs (se montará como volumen en compose)
RUN mkdir -p /app/logs

# ---------- 4) Puerto y comando ----------
ARG APP_PORT=8080          # <─ valor por defecto
ENV APP_PORT=${APP_PORT}

EXPOSE ${APP_PORT}

# Lanzamos uvicorn leyendo la var
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port $APP_PORT"]
