import os
import subprocess
from pathlib import Path


ENTRYPOINT = Path("scripts/telegram_bot_api_entrypoint.sh")


def test_entrypoint_reads_secret_files_and_executes_api_binary(tmp_path):
    api_id_file = tmp_path / "telegram_api_id"
    api_hash_file = tmp_path / "telegram_api_hash"
    output_file = tmp_path / "observed"
    fake_binary = tmp_path / "telegram-bot-api"
    api_id_file.write_text("123456\n")
    api_hash_file.write_text("dummy-api-hash\n")
    fake_binary.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n%s\\n%s\\n' \"$TELEGRAM_API_ID\" \"$TELEGRAM_API_HASH\" \"$*\" > \"$OUTPUT_FILE\"\n"
    )
    fake_binary.chmod(0o700)

    environment = os.environ.copy()
    environment.update(
        {
            "TELEGRAM_API_ID_FILE": str(api_id_file),
            "TELEGRAM_API_HASH_FILE": str(api_hash_file),
            "TELEGRAM_BOT_API_BINARY": str(fake_binary),
            "OUTPUT_FILE": str(output_file),
        }
    )

    result = subprocess.run(
        ["sh", str(ENTRYPOINT), "--local", "--http-port=8081"],
        cwd=Path(__file__).parents[1],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert output_file.read_text().splitlines() == [
        "123456",
        "dummy-api-hash",
        "--local --http-port=8081",
    ]


def test_entrypoint_rejects_missing_secret_without_echoing_values(tmp_path):
    api_hash_file = tmp_path / "telegram_api_hash"
    api_hash_file.write_text("should-not-be-logged\n")
    environment = os.environ.copy()
    environment.update(
        {
            "TELEGRAM_API_ID_FILE": str(tmp_path / "missing"),
            "TELEGRAM_API_HASH_FILE": str(api_hash_file),
            "TELEGRAM_BOT_API_BINARY": "/bin/true",
        }
    )

    result = subprocess.run(
        ["sh", str(ENTRYPOINT)],
        cwd=Path(__file__).parents[1],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "not readable" in result.stderr
    assert "should-not-be-logged" not in result.stderr
