#!/bin/bash
set -e

# Ensure cookies directory exists
mkdir -p /app/cookies

# Note: Individual account cookie files will be created by the account manager

# Ensure temp directory exists
mkdir -p /app/temp

# Execute the main command
exec python -m src.instagram_video_bot "$@" 