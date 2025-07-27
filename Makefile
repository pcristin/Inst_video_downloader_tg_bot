.PHONY: help build up down logs restart shell clean setup-2fa dev test-instagrapi test-proxies accounts-list accounts-status accounts-setup accounts-rotate accounts-reset accounts-warmup sessions-clean sessions-backup sessions-restore warmup warmup-batch warmup-available warmup-banned warmup-help

help: ## Show this help message
	@echo 'Instagram Video Downloader Bot - instagrapi version'
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
	@echo '  test-instagrapi  Test instagrapi integration'
	@echo '  test-proxies     Test proxy configuration'
	@echo ''
	@echo '👥 Account Management:'
	@echo '  accounts-list    List accounts with proxy assignments'
	@echo '  accounts-status  Show account status'
	@echo '  accounts-setup   Setup all accounts (create sessions)'
	@echo '  accounts-rotate  Rotate to next account'
	@echo '  accounts-reset   Reset banned accounts'
	@echo ''
	@echo '📁 Session Management:'
	@echo '  sessions-clean   Delete all session files'
	@echo '  sessions-backup  Backup session files'
	@echo '  sessions-restore Restore session files'
	@echo ''
	@echo '🔥 Account Warmup:'
	@echo '  warmup USERNAME=user1     Warm up specific account'
	@echo '  warmup-batch ACCOUNTS=... Warm up multiple accounts'
	@echo '  warmup-available          Warm up available accounts'
	@echo '  warmup-banned             Warm up banned accounts'
	@echo '  warmup-help               Show warmup examples'
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
	docker-compose exec instagram-video-bot python -m src.instagram_video_bot.utils.health_check

test-totp: ## Test TOTP code generation
	docker-compose run --rm --entrypoint python instagram-video-bot -m src.instagram_video_bot.test_totp 

test-instagrapi: ## Test instagrapi integration and login
	docker-compose run --rm --entrypoint python instagram-video-bot /app/test_instagrapi.py

test-proxies: ## Test proxy parsing and configuration
	@echo "🌐 Testing Proxy Configuration"
	@echo "Format: user:pass@host:port (http:// added automatically)"
	@docker-compose run --rm --entrypoint python instagram-video-bot -c "from src.instagram_video_bot.config.settings import settings; proxies = settings.get_proxy_list(); print(f'✅ Found {len(proxies)} proxies:'); [print(f'  {i+1}: {proxy}') for i, proxy in enumerate(proxies[:10])] if proxies else print('❌ No proxies configured in PROXIES environment variable')"

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
	docker-compose run --rm --entrypoint python instagram-video-bot /app/manage_accounts.py status

accounts-setup: ## Setup all accounts (login and create sessions)
	docker-compose run --rm --entrypoint python instagram-video-bot /app/manage_accounts.py setup

accounts-rotate: ## Manually rotate to next available account
	docker-compose run --rm --entrypoint python instagram-video-bot /app/manage_accounts.py rotate

accounts-reset: ## Reset banned status for all accounts
	docker-compose run --rm --entrypoint python instagram-video-bot /app/manage_accounts.py reset

accounts-reset-one: ## Reset banned status for specific account (usage: make accounts-reset-one USERNAME=username1)
	docker-compose run --rm --entrypoint python instagram-video-bot /app/manage_accounts.py reset $(USERNAME)

accounts-warmup: ## Warm up current account
	docker-compose run --rm --entrypoint python instagram-video-bot /app/manage_accounts.py warmup

accounts-warmup-one: ## Warm up specific account (usage: make accounts-warmup-one USERNAME=username1)
	docker-compose run --rm --entrypoint python instagram-video-bot /app/manage_accounts.py warmup $(USERNAME)

# Session Management Commands
sessions-clean: ## Clean all session files (forces fresh login for all accounts)
	docker-compose run --rm --entrypoint sh instagram-video-bot -c "rm -f /app/sessions/*.json && echo 'All session files deleted. Accounts will need to login again.'"

sessions-backup: ## Backup all session files
	docker-compose run --rm --entrypoint sh instagram-video-bot -c "mkdir -p /app/sessions/backup && cp /app/sessions/*.json /app/sessions/backup/ 2>/dev/null && echo 'Session files backed up to sessions/backup/' || echo 'No session files to backup'"

sessions-restore: ## Restore session files from backup
	docker-compose run --rm --entrypoint sh instagram-video-bot -c "cp /app/sessions/backup/*.json /app/sessions/ 2>/dev/null && echo 'Session files restored from backup' || echo 'No backup files found'"

# Enhanced Account Warmup Commands
warmup: ## Warm up specific account (usage: make warmup USERNAME=username1)
	@if [ -z "$(USERNAME)" ]; then \
		echo "❌ Error: USERNAME is required"; \
		echo "Usage: make warmup USERNAME=username1"; \
		exit 1; \
	fi
	docker-compose run --rm --entrypoint python instagram-video-bot /app/manage_accounts.py warmup $(USERNAME)

warmup-batch: ## Warm up multiple accounts with delays (usage: make warmup-batch ACCOUNTS="user1 user2 user3" DELAY=3600)
	@if [ -z "$(ACCOUNTS)" ]; then \
		echo "❌ Error: ACCOUNTS is required"; \
		echo "Usage: make warmup-batch ACCOUNTS=\"username1 username2\" DELAY=3600"; \
		exit 1; \
	fi
	@for account in $(ACCOUNTS); do \
		echo "🔥 Warming up account: $$account"; \
		docker-compose run --rm --entrypoint python instagram-video-bot /app/manage_accounts.py warmup $$account || true; \
		if [ "$(DELAY)" != "" ] && [ "$$account" != "$$(echo $(ACCOUNTS) | rev | cut -d' ' -f1 | rev)" ]; then \
			echo "⏰ Waiting $(DELAY) seconds before next account..."; \
			sleep $(DELAY); \
		fi; \
	done

warmup-available: ## Warm up all available (non-banned) accounts with 4 hour delays
	@echo "🔥 Starting batch warmup of available accounts..."
	@docker-compose run --rm --entrypoint python instagram-video-bot /app/manage_accounts.py status | grep "✅" | awk -F'|' '{print $$1}' | tr -d ' ' | head -3 > /tmp/available_accounts.txt
	@if [ ! -s /tmp/available_accounts.txt ]; then \
		echo "❌ No available accounts found"; \
		rm -f /tmp/available_accounts.txt; \
		exit 1; \
	fi
	@accounts=$$(cat /tmp/available_accounts.txt | tr '\n' ' ' | sed 's/ $$//'); \
	rm -f /tmp/available_accounts.txt; \
	echo "Found accounts: $$accounts"; \
	$(MAKE) warmup-batch ACCOUNTS="$$accounts" DELAY=14400

warmup-banned: ## Warm up all banned accounts to potentially restore them (usage: make warmup-banned)
	@echo "🔥 Starting warmup of banned accounts..."
	@docker-compose run --rm --entrypoint python instagram-video-bot /app/manage_accounts.py status | grep "❌" | awk -F'|' '{print $$1}' | tr -d ' ' > /tmp/banned_accounts.txt
	@if [ ! -s /tmp/banned_accounts.txt ]; then \
		echo "✅ No banned accounts found"; \
		rm -f /tmp/banned_accounts.txt; \
		exit 0; \
	fi
	@accounts=$$(cat /tmp/banned_accounts.txt | tr '\n' ' ' | sed 's/ $$//'); \
	rm -f /tmp/banned_accounts.txt; \
	echo "Found banned accounts: $$accounts"; \
	$(MAKE) warmup-batch ACCOUNTS="$$accounts" DELAY=7200

warmup-help: ## Show warmup command examples
	@echo "🔥 Account Warmup Commands (instagrapi-based):"
	@echo ""
	@echo "Single account:"
	@echo "  make warmup USERNAME=username1"
	@echo ""
	@echo "Multiple accounts with delays:"
	@echo "  make warmup-batch ACCOUNTS=\"username1 username2 username3\" DELAY=3600"
	@echo ""
	@echo "All available accounts (max 3, 4h delays):"
	@echo "  make warmup-available"
	@echo ""
	@echo "All banned accounts (2h delays):"
	@echo "  make warmup-banned"
	@echo ""
	@echo "Session management:"
	@echo "  make sessions-clean    # Force fresh login for all accounts"
	@echo "  make sessions-backup   # Backup current sessions"
	@echo "  make sessions-restore  # Restore sessions from backup"
	@echo ""
	@echo "💡 Recommended: Warm up 2-3 accounts per day with 4+ hour delays" 