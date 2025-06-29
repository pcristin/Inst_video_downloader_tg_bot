.PHONY: help build up down logs restart shell clean setup-2fa dev

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