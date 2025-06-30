.PHONY: help build up down logs restart shell clean setup-2fa dev accounts-status accounts-setup accounts-rotate accounts-reset accounts-warmup check-cookies import-cookies format-instmanager import-instmanager create-preauth monitor-cookies warmup warmup-batch warmup-available warmup-banned warmup-help

help: ## Show this help message
	@echo 'Usage: make [target]'
	@echo ''
	@echo 'Targets:'
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-15s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

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
	rm -rf temp/* logs/* 2fa_qr.png

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

# Account Management Commands
accounts-status: ## Show status of all Instagram accounts
	docker-compose run --rm --entrypoint python instagram-video-bot /app/manage_accounts.py status

accounts-setup: ## Setup all accounts (login and generate cookies)
	docker-compose run --rm --entrypoint python instagram-video-bot /app/manage_accounts.py setup

accounts-rotate: ## Manually rotate to next available account
	docker-compose run --rm --entrypoint python instagram-video-bot /app/manage_accounts.py rotate

accounts-reset: ## Reset banned status for all accounts
	docker-compose run --rm --entrypoint python instagram-video-bot /app/manage_accounts.py reset

accounts-reset-one: ## Reset banned status for specific account (usage: make accounts-reset-one USERNAME=samosirarlene)
	docker-compose run --rm --entrypoint python instagram-video-bot /app/manage_accounts.py reset $(USERNAME)

accounts-warmup: ## Warm up current account
	docker-compose run --rm --entrypoint python instagram-video-bot /app/manage_accounts.py warmup

accounts-warmup-one: ## Warm up specific account (usage: make accounts-warmup-one USERNAME=samosirarlene)
	docker-compose run --rm --entrypoint python instagram-video-bot /app/manage_accounts.py warmup $(USERNAME)

# Cookie Management Commands  
check-cookies: ## Check if Instagram cookies are valid
	docker-compose run --rm --entrypoint python instagram-video-bot /app/check_cookies.py

import-cookies: ## Import cookies from account.txt (single account mode)
	docker-compose run --rm --entrypoint python instagram-video-bot /app/import_cookies.py

format-instmanager: ## Format raw InstAccountsManager accounts (run on host)
	python3 format_instmanager_accounts.py

import-instmanager: ## Import InstAccountsManager format accounts from instmanager_accounts.txt
	docker-compose run --rm -v $(PWD)/instmanager_accounts.txt:/app/instmanager_accounts.txt --entrypoint python instagram-video-bot /app/import_cookies_instmanager.py

create-preauth: ## Create accounts_preauth.txt from imported cookie files
	docker-compose run --rm --entrypoint sh instagram-video-bot -c "ls /app/cookies/*_cookies.txt | sed 's/\/app\/cookies\///g' | sed 's/_cookies.txt//g' > /app/accounts_preauth.txt && echo 'Created accounts_preauth.txt with:' && cat /app/accounts_preauth.txt"

# Monitoring Commands
monitor-cookies: ## Start cookie health monitoring
	docker-compose run --rm --entrypoint python instagram-video-bot /app/monitor_cookies.py

# Enhanced Account Warmup Commands
warmup: ## Warm up specific account with browser simulation (usage: make warmup USERNAME=6118patriciaser.173)
	@if [ -z "$(USERNAME)" ]; then \
		echo "❌ Error: USERNAME is required"; \
		echo "Usage: make warmup USERNAME=6118patriciaser.173"; \
		exit 1; \
	fi
	docker-compose run --rm --entrypoint python instagram-video-bot /app/warmup_account.py $(USERNAME)

warmup-batch: ## Warm up multiple accounts with delays (usage: make warmup-batch ACCOUNTS="user1 user2 user3" DELAY=3600)
	@if [ -z "$(ACCOUNTS)" ]; then \
		echo "❌ Error: ACCOUNTS is required"; \
		echo "Usage: make warmup-batch ACCOUNTS=\"6118patriciaser.173 dr.elizabeth3771462\" DELAY=3600"; \
		exit 1; \
	fi
	@for account in $(ACCOUNTS); do \
		echo "🔥 Warming up account: $$account"; \
		docker-compose run --rm --entrypoint python instagram-video-bot /app/warmup_account.py $$account || true; \
		if [ "$(DELAY)" != "" ] && [ "$$account" != "$$(echo $(ACCOUNTS) | rev | cut -d' ' -f1 | rev)" ]; then \
			echo "⏰ Waiting $(DELAY) seconds before next account..."; \
			sleep $(DELAY); \
		fi; \
	done

warmup-available: ## Warm up all available (non-banned) accounts with 4 hour delays
	@echo "🔥 Starting batch warmup of available accounts..."
	@accounts=$$(docker-compose run --rm --entrypoint python instagram-video-bot /app/manage_accounts.py status | grep "✅" | awk -F'|' '{print $$2}' | tr -d ' ' | head -3); \
	if [ -z "$$accounts" ]; then \
		echo "❌ No available accounts found"; \
		exit 1; \
	fi; \
	make warmup-batch ACCOUNTS="$$accounts" DELAY=14400

warmup-banned: ## Warm up all banned accounts to potentially restore them (usage: make warmup-banned)
	@echo "🔥 Starting warmup of banned accounts..."
	@accounts=$$(docker-compose run --rm --entrypoint python instagram-video-bot /app/manage_accounts.py status | grep "❌" | awk -F'|' '{print $$2}' | tr -d ' '); \
	if [ -z "$$accounts" ]; then \
		echo "✅ No banned accounts found"; \
		exit 0; \
	fi; \
	make warmup-batch ACCOUNTS="$$accounts" DELAY=7200

warmup-help: ## Show warmup command examples
	@echo "🔥 Account Warmup Commands:"
	@echo ""
	@echo "Single account:"
	@echo "  make warmup USERNAME=6118patriciaser.173"
	@echo ""
	@echo "Multiple accounts with delays:"
	@echo "  make warmup-batch ACCOUNTS=\"user1 user2 user3\" DELAY=3600"
	@echo ""
	@echo "All available accounts (max 3, 4h delays):"
	@echo "  make warmup-available"
	@echo ""
	@echo "All banned accounts (2h delays):"
	@echo "  make warmup-banned"
	@echo ""
	@echo "💡 Recommended: Warm up 2-3 accounts per day with 4+ hour delays" 