FROM node:24.15.0-bookworm-slim@sha256:4e6b70dd6cbfc88c8157ba19aa3d9f9cce6ba4703576d55459e45efcbc9c5f5d AS builder
WORKDIR /build
COPY package.json package-lock.json* ./
RUN npm ci --no-audit --no-fund
COPY tsconfig.json vite.config.ts index.html ./
COPY src/ ./src/
RUN npm run build

FROM python:3.11.17-slim-bookworm@sha256:0a310eeecf4e1f5a0743f9a6520c90c88d089c903ca5fd283f501e3a805f5f89
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN groupadd --gid 10001 nuevamente \
    && useradd --uid 10001 --gid 10001 --no-create-home nuevamente
WORKDIR /app
COPY --from=builder /build/dist ./dist
COPY entrypoint.py ./entrypoint.py
ENV HOME=/tmp FRONTEND_HOST=0.0.0.0
USER 10001:10001
EXPOSE 8501
CMD ["python", "entrypoint.py"]
