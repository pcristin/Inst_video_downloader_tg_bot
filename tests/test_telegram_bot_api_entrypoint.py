import os
import socket
import subprocess
import threading
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
ENTRYPOINT_SOURCE = ROOT / "scripts/telegram_bot_api_entrypoint.c"
HEALTHCHECK_SOURCE = ROOT / "scripts/http_healthcheck.c"


def _compile(source: Path, output: Path) -> None:
    subprocess.run(
        [
            "cc",
            "-std=c11",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            str(source),
            "-o",
            str(output),
        ],
        check=True,
        text=True,
        capture_output=True,
    )


@pytest.fixture(scope="module")
def entrypoint_binary(tmp_path_factory):
    output = tmp_path_factory.mktemp("native-entrypoint") / "entrypoint"
    _compile(ENTRYPOINT_SOURCE, output)
    return output


@pytest.fixture(scope="module")
def healthcheck_binary(tmp_path_factory):
    output = tmp_path_factory.mktemp("native-healthcheck") / "healthcheck"
    _compile(HEALTHCHECK_SOURCE, output)
    return output


def _environment(
    api_id_file: Path,
    api_hash_file: Path,
    api_binary: str,
    media_dir: Path,
):
    environment = os.environ.copy()
    environment.update(
        {
            "TELEGRAM_API_ID_FILE": str(api_id_file),
            "TELEGRAM_API_HASH_FILE": str(api_hash_file),
            "TELEGRAM_BOT_API_BINARY": api_binary,
            "TELEGRAM_MEDIA_DIR": str(media_dir),
        }
    )
    return environment


def test_entrypoint_reads_secret_files_and_preserves_arguments(
    entrypoint_binary,
    tmp_path,
):
    api_id_file = tmp_path / "telegram_api_id"
    api_hash_file = tmp_path / "telegram_api_hash"
    output_file = tmp_path / "observed"
    fake_binary = tmp_path / "telegram-bot-api"
    api_id_file.write_text("123456\n")
    api_hash_file.write_text("dummy-api-hash\n")
    fake_binary.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n%s\\n%s\\n%s\\n' \"$TELEGRAM_API_ID\" \"$TELEGRAM_API_HASH\" \"$1\" \"$2\" > \"$OUTPUT_FILE\"\n"
    )
    fake_binary.chmod(0o700)
    environment = _environment(
        api_id_file,
        api_hash_file,
        str(fake_binary),
        tmp_path,
    )
    environment["OUTPUT_FILE"] = str(output_file)

    result = subprocess.run(
        [str(entrypoint_binary), "--local", "--http-port=8081"],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""
    assert output_file.read_text().splitlines() == [
        "123456",
        "dummy-api-hash",
        "--local",
        "--http-port=8081",
    ]


@pytest.mark.parametrize(
    ("invalid_kind", "invalid_content"),
    [
        ("missing", None),
        ("empty", ""),
        ("oversized", "x" * 4097),
        ("multiline", "first\nsecond\n"),
    ],
)
def test_entrypoint_rejects_invalid_secret_without_disclosure(
    entrypoint_binary,
    tmp_path,
    invalid_kind,
    invalid_content,
):
    api_id_file = tmp_path / "telegram_api_id"
    api_hash_file = tmp_path / "telegram_api_hash"
    api_hash_file.write_text("SECRET_HASH_MUST_NOT_BE_LOGGED\n")
    if invalid_content is not None:
        api_id_file.write_text(invalid_content)

    result = subprocess.run(
        [str(entrypoint_binary)],
        env=_environment(api_id_file, api_hash_file, "/bin/true", tmp_path),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert invalid_kind not in result.stderr
    assert "SECRET_HASH_MUST_NOT_BE_LOGGED" not in result.stderr
    if invalid_content:
        assert invalid_content not in result.stderr


def test_entrypoint_rejects_symlink_secret(entrypoint_binary, tmp_path):
    target = tmp_path / "real-api-id"
    api_id_file = tmp_path / "telegram_api_id"
    api_hash_file = tmp_path / "telegram_api_hash"
    target.write_text("SECRET_ID_MUST_NOT_BE_LOGGED\n")
    api_id_file.symlink_to(target)
    api_hash_file.write_text("SECRET_HASH_MUST_NOT_BE_LOGGED\n")

    result = subprocess.run(
        [str(entrypoint_binary)],
        env=_environment(api_id_file, api_hash_file, "/bin/true", tmp_path),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "SECRET_ID_MUST_NOT_BE_LOGGED" not in result.stderr
    assert "SECRET_HASH_MUST_NOT_BE_LOGGED" not in result.stderr


def test_entrypoint_rejects_inaccessible_media_directory_without_disclosure(
    entrypoint_binary,
    tmp_path,
):
    api_id_file = tmp_path / "telegram_api_id"
    api_hash_file = tmp_path / "telegram_api_hash"
    missing_media_dir = tmp_path / "private-media"
    api_id_file.write_text("123456\n")
    api_hash_file.write_text("dummy-api-hash\n")

    result = subprocess.run(
        [str(entrypoint_binary)],
        env=_environment(
            api_id_file,
            api_hash_file,
            "/bin/true",
            missing_media_dir,
        ),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert result.stdout == ""
    assert result.stderr == "Unable to access shared media directory\n"
    assert str(missing_media_dir) not in result.stderr


def test_healthcheck_accepts_any_http_response(healthcheck_binary):
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    def respond():
        connection, _ = server.accept()
        with connection:
            connection.recv(1024)
            connection.sendall(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
        server.close()

    thread = threading.Thread(target=respond)
    thread.start()
    try:
        result = subprocess.run(
            [str(healthcheck_binary), "127.0.0.1", str(port)],
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
    finally:
        thread.join(timeout=5)

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_healthcheck_fails_when_port_is_closed(healthcheck_binary):
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    port = server.getsockname()[1]
    server.close()

    result = subprocess.run(
        [str(healthcheck_binary), "127.0.0.1", str(port)],
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
    )

    assert result.returncode != 0
