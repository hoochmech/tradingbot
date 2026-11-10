"""Phone monitoring through a Telegram bot: push alerts, plus /status and /kill commands.

Setup (about 5 minutes, see README): create a bot with @BotFather, then set TELEGRAM_BOT_TOKEN and
TELEGRAM_CHAT_ID in .env. Commands are accepted only from that one chat id.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

log = logging.getLogger("bot.notify")


class Notifier:
    def __init__(self, token: str | None = None, chat_id: str | None = None):
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
        self.chat_id = str(chat_id or os.environ.get("TELEGRAM_CHAT_ID") or "")
        self.enabled = bool(self.token and self.chat_id)
        self._offset = 0

    def _call(self, method: str, params: dict, timeout: float = 10) -> dict:
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        data = urllib.parse.urlencode(params).encode()
        with urllib.request.urlopen(url, data=data, timeout=timeout) as r:
            return json.loads(r.read())

    def send(self, text: str) -> None:
        log.info("alert: %s", text.replace("\n", " | "))
        if not self.enabled:
            return
        try:
            self._call("sendMessage", {"chat_id": self.chat_id, "text": text})
        except Exception as e:  # alerts must never crash the bot
            log.warning("telegram send failed: %s", e)

    def listen(self, status: Callable[[], str], kill_switch_file: str) -> None:
        """Answer /status and /kill from the configured chat, in a background thread."""
        if not self.enabled:
            return

        def loop() -> None:
            while True:
                try:
                    res = self._call("getUpdates", {"offset": self._offset, "timeout": 50}, timeout=60)
                except Exception as e:
                    log.warning("telegram poll failed: %s", e)
                    threading.Event().wait(10)
                    continue
                for u in res.get("result", []):
                    self._offset = u["update_id"] + 1
                    msg = u.get("message") or {}
                    if str(msg.get("chat", {}).get("id")) != self.chat_id:
                        continue  # ignore everyone else
                    cmd = (msg.get("text") or "").strip().lower()
                    if cmd.startswith("/status"):
                        self.send(status())
                    elif cmd.startswith("/kill"):
                        Path(kill_switch_file).touch()
                        self.send("Kill switch set. The bot will close all positions and stop on the next bar.")
                    elif cmd.startswith("/help") or cmd.startswith("/start"):
                        self.send("/status - equity and open positions\n/kill - close everything and stop")

        threading.Thread(target=loop, daemon=True).start()
