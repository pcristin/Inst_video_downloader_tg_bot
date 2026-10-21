# Instagram Video Downloader Bot - tool-minimized runtime
FROM python:3.11-slim@sha256:cce12316475d7d56d605f76e1d811d0dc4e7227f2d4afd29951521264f504ff8 AS builder

COPY --from=ghcr.io/astral-sh/uv:0.12.0@sha256:606e70c71c852d03f611b1e56a195d08648507018a7057fab82c4974c4eae105 /uv /uvx /bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

WORKDIR /app

COPY pyproject.toml uv.lock .python-version ./
COPY vendor/instagrapi-3.0.16-1pillow13-py3-none-any.whl ./vendor/
RUN uv sync --frozen --no-dev --no-install-project


FROM python:3.11-slim@sha256:cce12316475d7d56d605f76e1d811d0dc4e7227f2d4afd29951521264f504ff8

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 botuser

WORKDIR /app

COPY --from=builder /app/.venv/ /app/.venv/
COPY src/ ./src/
COPY manage_accounts.py ./

RUN mkdir -p temp sessions \
    && chown -R botuser:botuser /app \
    && rm -rf /usr/local/lib/python3.11/site-packages/* \
        /usr/local/lib/python3.11/ensurepip \
        /usr/local/bin/pip* \
    && rm -f /bin/sh /bin/dash /usr/bin/apt* /usr/bin/dpkg* /usr/sbin/dpkg*

USER 1000:1000

CMD ["/app/.venv/bin/python", "-m", "src.instagram_video_bot"]
