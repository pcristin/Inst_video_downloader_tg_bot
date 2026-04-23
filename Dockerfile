# Instagram Video Downloader Bot - uv-native version
FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.1 /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

RUN apt-get update && apt-get install -y \
    curl \
    wget \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 botuser

WORKDIR /app

COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src/ ./src/
COPY manage_accounts.py ./

RUN mkdir -p temp sessions && \
    chown -R botuser:botuser /app/temp /app/sessions /app

USER botuser

CMD ["uv", "run", "--no-sync", "python", "-m", "src.instagram_video_bot"]
