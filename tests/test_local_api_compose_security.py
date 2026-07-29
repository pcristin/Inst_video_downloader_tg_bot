from pathlib import Path


COMPOSE_FILE = Path(__file__).parents[1] / "docker-compose.local-api.yml"
BASE_COMPOSE_FILE = Path(__file__).parents[1] / "docker-compose.yml"


def test_local_api_has_separate_egress_network() -> None:
    compose = COMPOSE_FILE.read_text()
    api_service = compose.split("  telegram-bot-api:\n", 1)[1].split(
        "\n  instagram-video-bot:", 1
    )[0]
    networks = compose.split("\nnetworks:\n", 1)[1].split("\nsecrets:\n", 1)[0]

    assert "      - telegram-api\n" in api_service
    assert "      - api-egress\n" in api_service
    assert "  api-egress:\n" in networks


def test_local_api_file_secrets_do_not_request_unsupported_permissions() -> None:
    compose = COMPOSE_FILE.read_text()
    api_service = compose.split("  telegram-bot-api:\n", 1)[1].split(
        "\n  instagram-video-bot:", 1
    )[0]
    secret_mounts = api_service.split("    secrets:\n", 1)[1].split(
        "    volumes:\n", 1
    )[0]

    assert "        uid:" not in secret_mounts
    assert "        gid:" not in secret_mounts
    assert "        mode:" not in secret_mounts


def test_bot_token_is_mounted_as_a_service_exclusive_file() -> None:
    compose = BASE_COMPOSE_FILE.read_text()
    bot_service = compose.split("  instagram-video-bot:\n", 1)[1].split(
        "\nsecrets:\n", 1
    )[0]

    assert "BOT_TOKEN_FILE=/run/secrets/telegram_bot_token" in bot_service
    assert "source: telegram_bot_token" in bot_service
    assert (
        "telegram_bot_token:\n    file: ./secrets/telegram_bot_token" in compose
    )

    local_compose = COMPOSE_FILE.read_text()
    api_service = local_compose.split("  telegram-bot-api:\n", 1)[1].split(
        "\n  instagram-video-bot:", 1
    )[0]
    assert "telegram_bot_token" not in api_service


def test_services_use_explicit_numeric_users_and_hardened_tmpfs() -> None:
    base_compose = BASE_COMPOSE_FILE.read_text()
    local_compose = COMPOSE_FILE.read_text()
    bot_service = base_compose.split("  instagram-video-bot:\n", 1)[1].split(
        "\nsecrets:\n", 1
    )[0]
    api_service = local_compose.split("  telegram-bot-api:\n", 1)[1].split(
        "\n  instagram-video-bot:", 1
    )[0]

    assert '    user: "1000:1000"' in bot_service
    assert '    user: "10001:10001"' in api_service
    assert (
        "/tmp:size=256m,mode=1770,uid=1000,gid=1000,noexec,nosuid,nodev"
        in bot_service
    )
    assert (
        "/tmp/telegram-bot-api:size=1g,mode=1770,uid=10001,gid=10001,noexec,nosuid,nodev"
        in api_service
    )
