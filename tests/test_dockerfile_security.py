import re
from pathlib import Path


DOCKERFILE = Path(__file__).parents[1] / "Dockerfile"
API_DOCKERFILE = Path(__file__).parents[1] / "Dockerfile.telegram-bot-api"


def test_runtime_image_removes_global_python_installers() -> None:
    dockerfile = DOCKERFILE.read_text()

    assert "/usr/local/lib/python3.11/site-packages/*" in dockerfile
    assert "/usr/local/lib/python3.11/ensurepip" in dockerfile
    assert "/usr/local/bin/pip*" in dockerfile


def test_external_images_are_pinned_by_digest() -> None:
    dockerfile = DOCKERFILE.read_text()
    api_dockerfile = API_DOCKERFILE.read_text()
    digest = r"sha256:[0-9a-f]{64}"

    assert re.search(rf"FROM python:3\.11-slim@{digest}", dockerfile)
    assert re.search(
        rf"COPY --from=ghcr\.io/astral-sh/uv:0\.12\.0@{digest}", dockerfile
    )
    assert len(re.findall(rf"FROM debian:bookworm-slim@{digest}", api_dockerfile)) == 2


def test_bot_runtime_removes_shell_and_package_manager_commands() -> None:
    dockerfile = DOCKERFILE.read_text()

    assert "rm -f /bin/sh /bin/dash" in dockerfile
    assert "/usr/bin/apt*" in dockerfile
    assert "/usr/bin/dpkg*" in dockerfile


def test_local_api_uses_native_helpers_without_final_stage_curl() -> None:
    dockerfile = API_DOCKERFILE.read_text()
    final_stage = dockerfile.split("\nFROM ", 2)[-1]

    assert "scripts/telegram_bot_api_entrypoint.c" in dockerfile
    assert "scripts/http_healthcheck.c" in dockerfile
    assert "telegram_bot_api_entrypoint.sh" not in dockerfile
    assert "curl" not in final_stage
    assert 'ENTRYPOINT ["/usr/local/bin/telegram-bot-api-entrypoint"]' in final_stage
    assert 'CMD ["/usr/local/bin/http-healthcheck"' in final_stage
    assert "rm -f /bin/sh /bin/dash" in final_stage
    assert "/usr/bin/apt*" in final_stage
    assert "/usr/bin/dpkg*" in final_stage
