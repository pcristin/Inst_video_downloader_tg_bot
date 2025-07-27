# Instagram Video Downloader Bot - instagrapi version
# Lightweight Docker image using instagrapi for Instagram API access
# No browser automation required - direct API communication

# Use Python 3.11 slim image as base
FROM python:3.11-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive

# Install system dependencies (minimal for instagrapi)
RUN apt-get update && apt-get install -y \
    # Basic utilities
    curl \
    wget \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m -u 1000 botuser

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/ ./src/

# Copy management scripts (instagrapi-based)
COPY manage_accounts.py test_instagrapi.py ./

# Copy example configuration files
COPY accounts_example.txt env_example_multi_proxy.txt INSTAGRAPI_MIGRATION.md ./

# Copy entrypoint script
COPY docker-entrypoint.sh /app/
RUN chmod +x /app/docker-entrypoint.sh

# Create necessary directories and set permissions
# temp: for video downloads
# sessions: for instagrapi session persistence
RUN mkdir -p temp sessions && \
    chown -R botuser:botuser /app

# Switch to non-root user
USER botuser