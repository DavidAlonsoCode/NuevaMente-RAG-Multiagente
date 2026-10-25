# Etapa 1: Compilar el Frontend React
FROM node:20-bookworm-slim AS frontend-builder
WORKDIR /build

COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund

COPY frontend/tsconfig.json frontend/vite.config.ts frontend/index.html ./
COPY frontend/src/ ./src/
RUN npm run build

# Etapa 2: Imagen final de ejecucion con Python
FROM python:3.11.9-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=10000 \
    DATA_DIR=/app/.data \
    PYTHONPATH=/app/backend

# Dependencias nativas para renderizado de fuentes y exportaciones PDF
RUN apt-get update && apt-get install -y --no-install-recommends \
        fonts-dejavu-core fontconfig libfreetype6 libjpeg62-turbo libpng16-16 libopenjp2-7 \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /app/.data /app/frontend /app/backend

WORKDIR /app

# Instalar requerimientos backend
COPY backend/requirements.txt ./backend/
RUN pip install --no-cache-dir -r ./backend/requirements.txt

# Copiar codigo de backend y servidor de frontend
COPY backend/app/ ./backend/app/
COPY frontend/entrypoint.py ./frontend/entrypoint.py
COPY --from=frontend-builder /build/dist ./frontend/dist

# Copiar supervisor de ejecucion
COPY runner.py ./runner.py

EXPOSE 10000

CMD ["python", "runner.py"]
