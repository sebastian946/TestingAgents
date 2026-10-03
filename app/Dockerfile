# One Dockerfile, two targets sharing the same code and dependencies:
#   --target api     : slim image, runs uvicorn
#   --target worker  : same + Chromium (only Chromium, not the 3 browsers) for Playwright
FROM ghcr.io/astral-sh/uv:latest AS uv

# ---------------------------------------------------------------- base
FROM python:3.12-slim AS base

COPY --from=uv /uv /uvx /bin/

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    PATH="/app/.venv/bin:$PATH"

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . /app

# ----------------------------------------------------------------- api
FROM base AS api

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

# -------------------------------------------------------------- worker
FROM base AS worker

# Chromium + its system libraries, matching the playwright version in uv.lock.
# PLAYWRIGHT_BROWSERS_PATH puts the browser in a fixed path readable by any user.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

CMD ["python", "-m", "worker.worker"]
