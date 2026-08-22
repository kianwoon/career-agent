# Career Agent API — production image
# FastAPI + LangGraph + Playwright (headless Chromium)
#
# Size note: includes headless Chromium (~350MB browser + ~300MB system libs).
# Using python:3.12-slim (no full python build deps) keeps it leaner.

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# System deps needed by Playwright's headless chromium (slim set).
# chromium uses: libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2
# libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2
# libgbm1 libasound2 libpango-1.0-0 libcairo2 libatspi2.0-0 libx11-6 libxcb1
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 \
    libasound2 libpango-1.0-0 libcairo2 libatspi2.0-0 libx11-6 libxcb1 \
    ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv (fast, small).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# Install project dependencies from lockfile.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Install Playwright's headless chromium ONLY (no --with-deps; libs above).
RUN uv run playwright install chromium

# Copy the application source.
COPY app ./app

EXPOSE 8000

# Run the API. DB schema is auto-created in the lifespan on boot.
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
