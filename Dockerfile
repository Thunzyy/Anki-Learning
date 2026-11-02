# syntax=docker/dockerfile:1
FROM node:22-bookworm-slim AS frontend
WORKDIR /build
COPY studio/web/package.json studio/web/package-lock.json ./
RUN npm ci
COPY studio/web/index.html studio/web/tsconfig.json studio/web/vite.config.ts ./
COPY studio/web/src ./src
RUN npm run build

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LEARNING_DATA_DIR=/var/lib/learning \
    LEARNING_WEB_DIR=/app/studio/web/dist
WORKDIR /app
COPY studio/api/requirements.txt /app/studio/api/requirements.txt
RUN pip install --no-cache-dir -r studio/api/requirements.txt \
    && useradd --create-home --uid 10001 learning \
    && mkdir -p /var/lib/learning \
    && chown learning:learning /var/lib/learning
COPY studio/api/app /app/studio/api/app
COPY data /app/data
COPY --from=frontend /build/dist /app/studio/web/dist
USER learning
EXPOSE 8700
HEALTHCHECK --interval=10s --timeout=3s --start-period=120s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8700/api/health', timeout=2)"
CMD ["python", "-m", "uvicorn", "app.main:app", "--app-dir", "studio/api", "--host", "0.0.0.0", "--port", "8700"]
