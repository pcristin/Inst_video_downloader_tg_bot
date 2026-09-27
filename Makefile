.PHONY: help build up down logs restart shell clean setup-2fa dev test-health test-proxies local-prepare local-config local-build local-up local-down local-logs accounts-list accounts-status accounts-setup accounts-rotate accounts-reset accounts-reset-old accounts-export-auth accounts-prewarm accounts-canary accounts-activate sessions-clean sessions-backup sessions-restore

COMPOSE ?= docker compose

help: ## Show this help message
	@echo 'Instagram Video Downloader Bot - uv-native workflow'
	@echo ''
	@echo 'Usage: make [target]'
	@echo ''
	@echo '🚀 Basic Operations:'
	@echo '  build            Build Docker image'
	@echo '  up               Start the bot'
	@echo '  down             Stop the bot'
	@echo '  restart          Restart the bot'
	@echo '  logs             View bot logs'
	@echo '  shell            Open shell in container'
	@echo '  clean            Clean up files and sessions'
	@echo '  local-config     Validate the Local Telegram Bot API stack'
	@echo '  local-build      Build the bot and Local Telegram Bot API'
	@echo '  local-up         Start the Local Telegram Bot API stack'
	@echo '  local-down       Stop the Local Telegram Bot API stack'
	@echo '  local-logs       Follow Local Telegram Bot API stack logs'
	@echo ''
	@echo '🔧 Testing:'
	@echo '  test-health      Test the health check'
	@echo '  test-proxies     Test proxy configuration'
	@echo ''
	@echo '👥 Account Management:'
	@echo '  accounts-list    List accounts with proxy assignments'
	@echo '  accounts-status  Show account status'
	@echo '  accounts-setup   Setup all accounts (create sessions)'
	@echo '  accounts-rotate  Rotate to next account'
	@echo '  accounts-reset   Reset banned accounts'
	@echo '  accounts-reset-old Reset accounts banned longer than HOURS (default 24)'
	@echo '  accounts-export-auth Export fast fallback cookies to secrets/instagram_auth.json'
	@echo '  accounts-prewarm Validate and log in a candidate roster (CANDIDATES=/absolute/path)'
	@echo '  accounts-canary  Download public media with each staged session (CANARY_URL=...)'
	@echo '  accounts-activate Stop bot, install canary-checked roster, and restart it'
	@echo ''
	@echo '📁 Session Management:'
	@echo '  sessions-clean   Delete all session files'
	@echo '  sessions-backup  Backup session files'
	@echo '  sessions-restore Restore session files'
	@echo ''
	@echo 'Proxy format: user:pass@host:port (http:// added automatically)'

build: ## Build the Docker image
	docker compose build

up: ## Start the bot in detached mode
	docker compose up -d

down: ## Stop the bot
	docker compose down

restart: ## Restart the bot
	docker compose restart

logs: ## View bot logs (follow mode)
	docker compose logs -f

local-prepare: ## Prepare least-privilege shared media access for Local Bot API
	install -d -o 1000 -g 1000 -m 0750 temp

local-config: local-prepare ## Validate the Local Telegram Bot API stack
	docker compose -f docker-compose.yml -f docker-compose.local-api.yml config --quiet

local-build: local-prepare ## Build the bot and Local Telegram Bot API images
	docker compose -f docker-compose.yml -f docker-compose.local-api.yml build

local-up: local-prepare ## Start the bot through the Local Telegram Bot API
	docker compose -f docker-compose.yml -f docker-compose.local-api.yml up -d

local-down: ## Stop the Local Telegram Bot API stack
	docker compose -f docker-compose.yml -f docker-compose.local-api.yml down

local-logs: ## Follow Local Telegram Bot API stack logs
	docker compose -f docker-compose.yml -f docker-compose.local-api.yml logs -f

shell: ## Open a shell in the running container
	docker compose exec instagram-video-bot /bin/bash

clean: ## Clean up temporary files and Docker volumes
	docker compose down -v
	rm -rf temp/* logs/* sessions/* 2fa_qr.png

setup-2fa: ## Set up two-factor authentication
	./docker-setup-2fa.sh

dev: ## Start in development mode with live reload
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up

dev-build: ## Build for development
	docker compose -f docker-compose.yml -f docker-compose.dev.yml build

test-health: ## Test the health check
	docker compose exec instagram-video-bot /app/.venv/bin/python -m src.instagram_video_bot.utils.health_check

test-proxies: ## Test proxy parsing and configuration
	@echo "🌐 Testing Proxy Configuration"
	@echo "Format: user:pass@host:port (http:// added automatically)"
	@docker compose run --rm --entrypoint /app/.venv/bin/python instagram-video-bot -c "from src.instagram_video_bot.config.settings import settings; print(f'Configured proxies: {len(settings.get_proxy_list())}')"

# Account Management Commands
accounts-list: ## List all accounts from accounts.txt with proxy assignments
	@docker compose run --rm --entrypoint /app/.venv/bin/python instagram-video-bot /app/manage_accounts.py list

accounts-status: ## Show status of all Instagram accounts
	docker compose run --rm --entrypoint /app/.venv/bin/python instagram-video-bot /app/manage_accounts.py status

accounts-setup: ## Setup all accounts (login and create sessions)
	docker compose run --rm --entrypoint /app/.venv/bin/python instagram-video-bot /app/manage_accounts.py setup

accounts-rotate: ## Manually rotate to next available account
	docker compose run --rm --entrypoint /app/.venv/bin/python instagram-video-bot /app/manage_accounts.py rotate

accounts-reset: ## Reset banned status for all accounts
	docker compose run --rm --entrypoint /app/.venv/bin/python instagram-video-bot /app/manage_accounts.py reset

accounts-reset-old: ## Reset accounts banned longer than HOURS hours (default 24)
	docker compose run --rm --entrypoint /app/.venv/bin/python instagram-video-bot /app/manage_accounts.py reset-old --hours $(if $(HOURS),$(HOURS),24)

accounts-export-auth: ## Export fast fallback cookies from configured Instagram accounts
	@mkdir -p secrets
	@test -f secrets/instagram_auth.json || printf '%s\n' '{"instagram":[],"instagram_bearer":[]}' > secrets/instagram_auth.json
	$(COMPOSE) run --rm --user root --cap-add DAC_OVERRIDE --cap-add CHOWN --entrypoint /app/.venv/bin/python -v ./secrets:/app/secrets instagram-video-bot /app/manage_accounts.py export-auth --runtime-uid 1000

accounts-prewarm: ## Validate and prewarm every candidate, preserving successful sessions
	@test -n "$(CANDIDATES)" || { echo 'Set CANDIDATES=/absolute/path/to/accounts-file'; exit 2; }
	uv run --frozen python rotate_accounts.py prewarm --candidates "$(CANDIDATES)" $(if $(SEED_SESSIONS),--seed-sessions "$(SEED_SESSIONS)",)

accounts-canary: ## Check each staged account with a real public media download
	@test -n "$$CANDIDATES" || { echo 'Set CANDIDATES=/absolute/path/to/accounts-file'; exit 2; }
	@test -n "$$CANARY_URL" || { echo 'Set CANARY_URL=https://www.instagram.com/p/.../'; exit 2; }
	uv run --frozen python rotate_accounts.py canary --candidates "$$CANDIDATES" --canary-url "$$CANARY_URL"

accounts-activate: ## Stop bot, install checked roster, then restart
	@test -n "$(CANDIDATES)" || { echo 'Set CANDIDATES=/absolute/path/to/accounts-file'; exit 2; }
	@$(COMPOSE) run --rm --entrypoint /app/.venv/bin/python instagram-video-bot -c 'import os; from pathlib import Path; p=Path("/app/accounts.txt"); assert p.stat().st_uid == os.getuid(), "current roster owner differs from runtime user"; assert os.access("/app/sessions", os.W_OK), "session directory is not writable"; assert os.access("/app/account-state", os.W_OK), "state directory is not writable"'
	@$(COMPOSE) stop instagram-video-bot && { uv run --frozen python rotate_accounts.py activate --candidates "$(CANDIDATES)"; result=$$?; $(COMPOSE) up -d --force-recreate --no-deps instagram-video-bot || exit $$?; exit $$result; }

# Session Management Commands
sessions-clean: ## Clean all session files (forces fresh login for all accounts)
	docker compose run --rm --entrypoint sh instagram-video-bot -c "rm -f /app/sessions/*.json && echo 'All session files deleted. Accounts will need to login again.'"

sessions-backup: ## Backup all session files
	docker compose run --rm --entrypoint sh instagram-video-bot -c "mkdir -p /app/sessions/backup && cp /app/sessions/*.json /app/sessions/backup/ 2>/dev/null && echo 'Session files backed up to sessions/backup/' || echo 'No session files to backup'"

sessions-restore: ## Restore session files from backup
	docker compose run --rm --entrypoint sh instagram-video-bot -c "cp /app/sessions/backup/*.json /app/sessions/ 2>/dev/null && echo 'Session files restored from backup' || echo 'No backup files found'"
