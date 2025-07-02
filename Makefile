# Instagram Video Downloader Bot - Makefile
.PHONY: help build up down logs restart shell test clean install dev lint format check accounts-status accounts-rotate accounts-setup accounts-reset check-cookies import-cookies import-instmanager monitor-cookies warmup warmup-batch warmup-available warmup-banned test-proxies proxy-status

# Default help command
help:
	@echo "Instagram Video Downloader Bot - Available Commands:"
	@echo ""
	@echo "🚀 Basic Operations:"
	@echo "  make build           - Build Docker image"
	@echo "  make up              - Start the bot"
	@echo "  make down            - Stop the bot"
	@echo "  make restart         - Restart the bot"
	@echo "  make logs            - View bot logs"
	@echo "  make shell           - Open shell in container"
	@echo ""
	@echo "🔐 Authentication & Cookies:"
	@echo "  make check-cookies   - Test if cookies are valid"
	@echo "  make import-cookies  - Import cookies from account.txt"
	@echo "  make import-instmanager - Import from InstAccountsManager"
	@echo "  make monitor-cookies - Monitor cookie health (background)"
	@echo ""
	@echo "👥 Multi-Account Management:"
	@echo "  make accounts-status - Show account status"
	@echo "  make accounts-setup  - Setup all accounts"
	@echo "  make accounts-rotate - Rotate to next account"
	@echo "  make accounts-reset  - Reset banned accounts"
	@echo ""
	@echo "🌐 Proxy Management:"
	@echo "  make test-proxies    - Test all configured proxies"
	@echo "  make proxy-status    - Show proxy assignments"
	@echo "  💡 Proxy format: PROXY_1=login:password@ip:port"
	@echo ""
	@echo "🔥 Account Warmup:"
	@echo "  make warmup USERNAME=name - Warm up specific account"
	@echo "  make warmup-batch         - Warm up multiple accounts"
	@echo "  make warmup-available     - Warm up available accounts"
	@echo "  make warmup-banned        - Warm up banned accounts"
	@echo ""
	@echo "🛠️  Development:"
	@echo "  make install         - Install dependencies"
	@echo "  make test            - Run tests"
	@echo "  make lint            - Run code linting"
	@echo "  make format          - Format code"
	@echo "  make clean           - Clean up files"

# Docker operations
build:
	docker-compose build

up:
	docker-compose up -d

down:
	docker-compose down

restart:
	docker-compose restart

logs:
	docker-compose logs -f

shell:
	docker-compose run --rm --entrypoint /bin/bash instagram-video-bot

# Cookie and authentication management
check-cookies:
	docker-compose run --rm --entrypoint python instagram-video-bot /app/check_cookies.py

import-cookies:
	docker-compose run --rm --entrypoint python instagram-video-bot /app/import_cookies.py

import-instmanager:
	docker-compose run --rm -v $(shell pwd)/instmanager_accounts.txt:/app/instmanager_accounts.txt --entrypoint python instagram-video-bot /app/import_cookies_instmanager.py

monitor-cookies:
	docker-compose run --rm -d --entrypoint python instagram-video-bot /app/monitor_cookies.py

# Multi-account management
accounts-status:
	docker-compose run --rm --entrypoint python instagram-video-bot /app/manage_accounts.py status

accounts-setup:
	docker-compose run --rm --entrypoint python instagram-video-bot /app/manage_accounts.py setup

accounts-rotate:
	docker-compose run --rm --entrypoint python instagram-video-bot /app/manage_accounts.py rotate

accounts-reset:
	docker-compose run --rm --entrypoint python instagram-video-bot /app/manage_accounts.py reset

# Proxy management
test-proxies:
	docker-compose run --rm --entrypoint python instagram-video-bot /app/test_proxies.py

proxy-status: test-proxies

# Account warmup
warmup:
	@if [ -z "$(USERNAME)" ]; then \
		echo "❌ Error: USERNAME is required"; \
		echo "Usage: make warmup USERNAME=accountname"; \
		exit 1; \
	fi
	docker-compose run --rm --entrypoint python instagram-video-bot /app/warmup_account.py $(USERNAME)

warmup-batch:
	@echo "🔥 Warming up multiple accounts with delays..."
	@if [ ! -f accounts_preauth.txt ]; then \
		echo "❌ accounts_preauth.txt not found"; \
		exit 1; \
	fi
	@while read line; do \
		if [ -n "$$line" ] && [ "$${line#\#}" = "$$line" ]; then \
			echo "🔥 Warming up: $$line"; \
			docker-compose run --rm --entrypoint python instagram-video-bot /app/warmup_account.py $$line || true; \
			echo "⏰ Waiting 30 seconds before next account..."; \
			sleep 30; \
		fi; \
	done < accounts_preauth.txt
	@echo "✅ Batch warmup completed!"

warmup-available:
	@echo "🔥 Warming up available (non-banned) accounts..."
	docker-compose run --rm --entrypoint python instagram-video-bot /app/manage_accounts.py warmup-available

warmup-banned:
	@echo "🔥 Attempting to warm up banned accounts..."
	docker-compose run --rm --entrypoint python instagram-video-bot /app/manage_accounts.py warmup-banned

# Development
install:
	pip install -r requirements.txt
	playwright install chromium

test:
	python -m pytest tests/ -v

lint:
	python -m pylint src/
	python -m mypy src/

format:
	python -m black src/
	python -m isort src/

check: lint test

clean:
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -delete
	rm -rf temp/*
	rm -rf logs/*

dev:
	docker-compose -f docker-compose.yml -f docker-compose.dev.yml up 