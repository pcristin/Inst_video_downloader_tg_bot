import logging

from src.instagram_video_bot.utils.proxy_manager import ProxyManager


def test_invalid_proxy_configuration_does_not_log_credentials(monkeypatch, caplog):
    secret = "proxy-user:proxy-pass@proxy.example:not-a-port"
    monkeypatch.setenv("PROXY_LIST", secret)
    monkeypatch.delenv("PROXY_HOST", raising=False)
    monkeypatch.delenv("PROXY_PORT", raising=False)
    for index in range(1, 21):
        monkeypatch.delenv(f"PROXY_{index}", raising=False)

    with caplog.at_level(
        logging.WARNING,
        logger="src.instagram_video_bot.utils.proxy_manager",
    ):
        ProxyManager()

    assert secret not in caplog.text
    assert "proxy-user" not in caplog.text
    assert "proxy-pass" not in caplog.text
    assert "Failed to parse proxy entry from PROXY_LIST" in caplog.text
