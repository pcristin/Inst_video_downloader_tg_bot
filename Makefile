.PHONY: help build up down logs restart shell clean setup-2fa dev accounts-status accounts-setup accounts-rotate accounts-reset accounts-warmup check-cookies import-cookies format-instmanager import-instmanager create-preauth monitor-cookies

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
	docker-compose run --rm -v ./instmanager_accounts.txt:/app/instmanager_accounts.txt --entrypoint python instagram-video-bot /app/import_cookies_instmanager.py

create-preauth: ## Create accounts_preauth.txt from imported cookie files
	docker-compose run --rm --entrypoint sh instagram-video-bot -c "ls /app/cookies/*_cookies.txt | sed 's/\/app\/cookies\///g' | sed 's/_cookies.txt//g' > /app/accounts_preauth.txt && echo 'Created accounts_preauth.txt with:' && cat /app/accounts_preauth.txt"

# Monitoring Commands
monitor-cookies: ## Start cookie health monitoring
	docker-compose run --rm --entrypoint python instagram-video-bot /app/monitor_cookies.py 