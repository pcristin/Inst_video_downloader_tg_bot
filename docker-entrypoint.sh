#!/bin/bash
set -e

# Ensure cookies directory exists
mkdir -p /app/cookies

# Ensure cookies file exists as a file, not a directory
COOKIES_FILE="/app/cookies/instagram_cookies.txt"

if [ -d "$COOKIES_FILE" ]; then
    echo "Removing cookies directory and creating file..."
    rm -rf "$COOKIES_FILE"
fi

if [ ! -f "$COOKIES_FILE" ]; then
    echo "Creating cookies file..."
    touch "$COOKIES_FILE"
fi

# Ensure temp directory exists
mkdir -p /app/temp

# Execute the main command
exec python -m src.instagram_video_bot "$@" 