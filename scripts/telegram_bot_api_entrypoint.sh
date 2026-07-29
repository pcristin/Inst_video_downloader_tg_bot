#!/bin/sh
set -eu

api_id_file=${TELEGRAM_API_ID_FILE:-/run/secrets/telegram_api_id}
api_hash_file=${TELEGRAM_API_HASH_FILE:-/run/secrets/telegram_api_hash}
api_binary=${TELEGRAM_BOT_API_BINARY:-telegram-bot-api}

for secret_file in "$api_id_file" "$api_hash_file"; do
    if [ ! -r "$secret_file" ]; then
        printf 'Telegram API credential file is not readable: %s\n' "$secret_file" >&2
        exit 1
    fi
done

TELEGRAM_API_ID=$(cat "$api_id_file")
TELEGRAM_API_HASH=$(cat "$api_hash_file")

if [ -z "$TELEGRAM_API_ID" ] || [ -z "$TELEGRAM_API_HASH" ]; then
    printf 'Telegram API credential files must not be empty\n' >&2
    exit 1
fi

export TELEGRAM_API_ID TELEGRAM_API_HASH
exec "$api_binary" "$@"
