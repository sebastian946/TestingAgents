# One Dockerfile, two targets sharing the same code and dependencies:
#   --target api     : slim image, runs uvicorn
#   --target worker  : same + Chromium (only Chromium, not the 3 browsers) for Playwright
#
# Both run as the unprivileged user `app` (WTA-30): the worker drives a browser over
# third-party sites, and the API faces the internet. A compromise of either stays a
# compromise of an unprivileged process in a container with no capabilities
# (docker-compose.yml drops them all).
FROM ghcr.io/astral-sh/uv:latest AS uv

# ---------------------------------------------------------------- base
FROM python:3.14-slim AS base

COPY --from=uv /uv /uvx /bin/

RUN groupadd --gid 1000 app && useradd --uid 1000 --gid app --create-home app

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    PATH="/app/.venv/bin:$PATH"

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . /app
# The reports volume is created from this folder's ownership the first time, so it must
# belong to `app` here; mkdir keeps the chown cheap (no need to chown the venv)
RUN mkdir -p /app/reports && chown -R app:app /app/reports /app/alembic

# ----------------------------------------------------------------- api
FROM base AS api

USER app
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

# -------------------------------------------------------------- worker
FROM base AS worker

# Chromium + its system libraries, matching the playwright version in uv.lock.
# PLAYWRIGHT_BROWSERS_PATH puts the browser in a fixed path readable by any user.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* \
    && chmod -R a+rX /ms-playwright

USER app
CMD ["python", "-m", "worker.worker"]
