from bot.notify import Notifier


def test_notifier_disabled_without_credentials(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    n = Notifier()
    assert not n.enabled
    n.send("no-op")  # must not raise or hit the network
