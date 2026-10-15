FROM node:20-bookworm-slim AS builder
WORKDIR /build
COPY package.json package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY tsconfig.json vite.config.ts index.html ./
COPY src/ ./src/
RUN npm run build

FROM python:3.11.17-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN groupadd --gid 10001 nuevamente \
    && useradd --uid 10001 --gid 10001 --no-create-home nuevamente
WORKDIR /app
COPY --from=builder /build/dist ./dist
COPY entrypoint.py ./entrypoint.py
ENV HOME=/tmp
USER 10001:10001
EXPOSE 8501
CMD ["python", "entrypoint.py"]
