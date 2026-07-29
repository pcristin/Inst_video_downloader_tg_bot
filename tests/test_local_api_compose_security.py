from pathlib import Path


COMPOSE_FILE = Path(__file__).parents[1] / "docker-compose.local-api.yml"


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
