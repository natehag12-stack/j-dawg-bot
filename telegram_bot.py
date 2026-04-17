"""
Telegram notifier — zero deps beyond `requests`.
"""
from __future__ import annotations
import requests


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = str(chat_id)
        self.base = f"https://api.telegram.org/bot{token}"

    def send(self, text: str) -> None:
        try:
            r = requests.post(
                f"{self.base}/sendMessage",
                json={
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": True,
                },
                timeout=10,
            )
            if r.status_code != 200:
                print(f"[telegram] failed: {r.status_code} {r.text}")
        except Exception as e:
            print(f"[telegram] error: {e}")

    def send_signal(
        self,
        *,
        symbol: str,
        side: str,
        entry: float,
        stop: float,
        target: float,
        confidence: float,
        reason: str,
        samples: int,
    ) -> None:
        emoji = "🟢" if side == "long" else "🔴"
        arrow = "▲" if side == "long" else "▼"
        rr_risk = abs(entry - stop)
        reward = abs(target - entry)
        rr = reward / rr_risk if rr_risk else 0.0

        msg = (
            f"{emoji} *PB THEORY {side.upper()} — {symbol}* {arrow}\n"
            f"```\n"
            f"Entry  : {entry:,.2f}\n"
            f"Stop   : {stop:,.2f}\n"
            f"Target : {target:,.2f}\n"
            f"R:R    : 1:{rr:.2f}\n"
            f"```\n"
            f"*Model confidence:* `{confidence*100:.1f}%`  "
            f"_(after {samples} closed trades)_\n"
            f"*Setup:* {reason}"
        )
        self.send(msg)

    def send_startup(self, symbol: str, bayes_summary: str) -> None:
        self.send(
            f"🤖 *J-Dawg Bot online*\n"
            f"Watching *{symbol}* on 5m with 1H bias filter.\n\n"
            f"```\n{bayes_summary}\n```"
        )
