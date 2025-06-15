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

# Create a properly formatted Netscape cookies file if it doesn't exist or is empty
if [ ! -f "$COOKIES_FILE" ] || [ ! -s "$COOKIES_FILE" ]; then
    echo "Creating properly formatted cookies file..."
    cat > "$COOKIES_FILE" << 'EOF'
# Netscape HTTP Cookie File
# https://curl.haxx.se/rfc/cookie_spec.html
# This is a generated file!  Do not edit.

EOF
fi

# Ensure temp directory exists
mkdir -p /app/temp

# Execute the main command
exec python -m src.instagram_video_bot "$@" 