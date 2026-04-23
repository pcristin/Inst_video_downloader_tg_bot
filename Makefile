.PHONY: help build up down logs restart shell clean setup-2fa dev test-health test-proxies accounts-list accounts-status accounts-setup accounts-rotate accounts-reset accounts-reset-old sessions-clean sessions-backup sessions-restore

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
	@echo ''
	@echo '📁 Session Management:'
	@echo '  sessions-clean   Delete all session files'
	@echo '  sessions-backup  Backup session files'
	@echo '  sessions-restore Restore session files'
	@echo ''
	@echo 'Proxy format: user:pass@host:port (http:// added automatically)'

build: ## Build the Docker image
	docker-compose build

up: ## Start the bot in detached mode
	docker-compose up -d

down: ## Stop the bot
	docker-compose down

restart: ## Restart the bot
	docker-compose restart

logs: ## View bot logs (follow mode)
	docker-compose logs -f

shell: ## Open a shell in the running container
	docker-compose exec instagram-video-bot /bin/bash

clean: ## Clean up temporary files and Docker volumes
	docker-compose down -v
	rm -rf temp/* logs/* sessions/* 2fa_qr.png

setup-2fa: ## Set up two-factor authentication
	./docker-setup-2fa.sh

dev: ## Start in development mode with live reload
	docker-compose -f docker-compose.yml -f docker-compose.dev.yml up

dev-build: ## Build for development
	docker-compose -f docker-compose.yml -f docker-compose.dev.yml build

test-health: ## Test the health check
	docker-compose exec instagram-video-bot uv run --no-sync python -m src.instagram_video_bot.utils.health_check

test-proxies: ## Test proxy parsing and configuration
	@echo "🌐 Testing Proxy Configuration"
	@echo "Format: user:pass@host:port (http:// added automatically)"
	@docker-compose run --rm --entrypoint uv instagram-video-bot run --no-sync python -c "from src.instagram_video_bot.config.settings import settings; proxies = settings.get_proxy_list(); print(f'✅ Found {len(proxies)} proxies:'); [print(f'  {i+1}: {proxy}') for i, proxy in enumerate(proxies[:10])] if proxies else print('❌ No proxies configured in PROXIES environment variable')"

# Account Management Commands
accounts-list: ## List all accounts from accounts.txt with proxy assignments
	@echo "📋 Accounts Configuration:"
	@if [ -f accounts.txt ]; then \
		echo ""; \
		cat -n accounts.txt | head -10 | while read line; do \
			echo "$$line"; \
		done; \
		echo ""; \
		echo "💡 Total accounts: $$(wc -l < accounts.txt 2>/dev/null || echo 0)"; \
	else \
		echo "❌ accounts.txt not found"; \
		echo "Create accounts.txt with format: username|password|totp_secret"; \
	fi

accounts-status: ## Show status of all Instagram accounts
	docker-compose run --rm --entrypoint uv instagram-video-bot run --no-sync python /app/manage_accounts.py status

accounts-setup: ## Setup all accounts (login and create sessions)
	docker-compose run --rm --entrypoint uv instagram-video-bot run --no-sync python /app/manage_accounts.py setup

accounts-rotate: ## Manually rotate to next available account
	docker-compose run --rm --entrypoint uv instagram-video-bot run --no-sync python /app/manage_accounts.py rotate

accounts-reset: ## Reset banned status for all accounts
	docker-compose run --rm --entrypoint uv instagram-video-bot run --no-sync python /app/manage_accounts.py reset

accounts-reset-old: ## Reset accounts banned longer than HOURS hours (default 24)
	docker-compose run --rm --entrypoint uv instagram-video-bot run --no-sync python /app/manage_accounts.py reset-old --hours $(if $(HOURS),$(HOURS),24)

# Session Management Commands
sessions-clean: ## Clean all session files (forces fresh login for all accounts)
	docker-compose run --rm --entrypoint sh instagram-video-bot -c "rm -f /app/sessions/*.json && echo 'All session files deleted. Accounts will need to login again.'"

sessions-backup: ## Backup all session files
	docker-compose run --rm --entrypoint sh instagram-video-bot -c "mkdir -p /app/sessions/backup && cp /app/sessions/*.json /app/sessions/backup/ 2>/dev/null && echo 'Session files backed up to sessions/backup/' || echo 'No session files to backup'"

sessions-restore: ## Restore session files from backup
	docker-compose run --rm --entrypoint sh instagram-video-bot -c "cp /app/sessions/backup/*.json /app/sessions/ 2>/dev/null && echo 'Session files restored from backup' || echo 'No backup files found'"
