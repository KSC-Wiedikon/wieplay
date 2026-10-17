# WiePlay API image: the FastAPI backend serving the built web app.
#   docker build -t wieplay-api:<tag> .

FROM node:24-alpine AS web
WORKDIR /src/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY docs/brand /src/docs/brand
COPY web/ ./
RUN npm run build

FROM python:3.12-slim AS api
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
COPY --from=ghcr.io/astral-sh/uv:0.11.11 /uv /usr/local/bin/uv
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/ ./
RUN uv sync --frozen --no-dev && useradd --system --uid 10001 wieplay
COPY --from=web /src/web/dist /app/web-dist
ENV PATH="/app/.venv/bin:$PATH" \
    WIEPLAY_WEB_DIST=/app/web-dist
USER wieplay
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2).status != 200)"]
CMD ["uvicorn", "--factory", "wieplay.api.app:asgi", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--no-server-header"]
