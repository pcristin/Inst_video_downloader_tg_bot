from pathlib import Path


def test_accounts_export_auth_runs_containerized_exporter() -> None:
    makefile = Path("Makefile").read_text(encoding="utf-8")
    _, target = makefile.split("accounts-export-auth:", maxsplit=1)
    recipe = target.split("\n\n", maxsplit=1)[0]

    assert "uv run --frozen python manage_accounts.py export-auth" not in recipe
    assert "docker compose run" in recipe
    assert "--user root" in recipe
    assert "-v ./secrets:/app/secrets" in recipe
    assert "python /app/manage_accounts.py export-auth" in recipe
    assert "chown -R 1000:1000 /app/sessions /app/secrets/instagram_auth.json" in recipe
