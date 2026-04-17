"""
Telegram notifier — zero deps beyond `requests`.

Also supports lightweight polling for inbound commands like /stats.
"""
from __future__ import annotations
import requests


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = str(chat_id)
        self.base = f"https://api.telegram.org/bot{token}"
        self._last_update_id = 0  # for getUpdates long-poll cursor

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
        tick_size: float = 0.01,
        paper: bool = True,
    ) -> None:
        emoji = "🟢" if side == "long" else "🔴"
        arrow = "▲" if side == "long" else "▼"
        rr_risk = abs(entry - stop)
        reward = abs(target - entry)
        rr = reward / rr_risk if rr_risk else 0.0
        risk_ticks = rr_risk / tick_size if tick_size else 0.0
        reward_ticks = reward / tick_size if tick_size else 0.0
        header = "📒 *PAPER OPEN*" if paper else "📣 *SIGNAL*"

        msg = (
            f"{header}  {emoji} *{side.upper()} — {symbol}* {arrow}\n"
            f"```\n"
            f"Entry  : {entry:,.2f}\n"
            f"Stop   : {stop:,.2f}  ({risk_ticks:.0f} ticks risk)\n"
            f"Target : {target:,.2f}  ({reward_ticks:.0f} ticks goal)\n"
            f"R:R    : 1:{rr:.2f}\n"
            f"```\n"
            f"*Model confidence:* `{confidence*100:.1f}%`  "
            f"_(after {samples} closed trades)_\n"
            f"*Setup:* {reason}"
        )
        self.send(msg)

    def send_close(
        self,
        *,
        symbol: str,
        side: str,
        entry: float,
        exit_price: float,
        pnl_r: float,
        exit_reason: str,
        tick_size: float,
        held: str,
    ) -> None:
        if side == "long":
            ticks = (exit_price - entry) / tick_size if tick_size else 0.0
        else:
            ticks = (entry - exit_price) / tick_size if tick_size else 0.0
        won = pnl_r > 0
        emoji = "✅" if won else "❌"
        verdict = "WIN" if won else "LOSS"
        msg = (
            f"{emoji} *PAPER CLOSE — {symbol} {side.upper()}*  ({verdict})\n"
            f"```\n"
            f"Entry  : {entry:,.2f}\n"
            f"Exit   : {exit_price:,.2f}\n"
            f"P&L    : {ticks:+.0f} ticks  ({pnl_r:+.2f}R)\n"
            f"Held   : {held}\n"
            f"Reason : {exit_reason}\n"
            f"```"
        )
        self.send(msg)

    def send_startup(self, symbols, bayes_summary: str) -> None:
        if isinstance(symbols, (list, tuple)):
            symstr = ", ".join(symbols)
        else:
            symstr = symbols
        self.send(
            f"🤖 *J-Dawg Bot online*\n"
            f"Watching *{symstr}* on 5m with 1H bias filter.\n\n"
            f"```\n{bayes_summary}\n```"
        )

    def send_daily_summary(self, date_str: str, rows: list, per_symbol: dict) -> None:
        wins = sum(1 for r in rows if r["outcome"] == "win")
        losses = sum(1 for r in rows if r["outcome"] == "loss")
        total_r = sum((r["pnl_r"] or 0.0) for r in rows)
        n = wins + losses
        wr = (wins / n * 100) if n else 0.0
        body = (
            f"Trades : {n}\n"
            f"Wins   : {wins}\n"
            f"Losses : {losses}\n"
            f"Win %  : {wr:.1f}%\n"
            f"Net R  : {total_r:+.2f}"
        )
        per_lines = []
        for sym, s in per_symbol.items():
            sn = s["wins"] + s["losses"]
            swr = (s["wins"] / sn * 100) if sn else 0.0
            per_lines.append(f"{sym:<8} {sn:>2}t  {swr:5.1f}%  {s['pnl_r']:+.2f}R")
        per_block = ("\n".join(per_lines)) if per_lines else "(no trades)"
        self.send(
            f"📊 *Daily P&L — {date_str}*\n"
            f"```\n{body}\n```\n"
            f"*Per symbol:*\n```\n{per_block}\n```"
        )

    def send_stats(self, overall: dict, per_symbol: dict, bayes_summary: str, thresholds: dict | None = None) -> None:
        n = overall["wins"] + overall["losses"]
        wr = (overall["wins"] / n * 100) if n else 0.0
        body = (
            f"Total trades : {n}\n"
            f"Wins         : {overall['wins']}\n"
            f"Losses       : {overall['losses']}\n"
            f"Pending      : {overall['pending']}\n"
            f"Win %        : {wr:.1f}%\n"
            f"Net R        : {overall['total_r']:+.2f}"
        )
        per_lines = []
        for sym, s in per_symbol.items():
            sn = s["wins"] + s["losses"]
            swr = (s["wins"] / sn * 100) if sn else 0.0
            per_lines.append(f"{sym:<8} {sn:>3}t  {swr:5.1f}%  {s['total_r']:+.2f}R")
        per_block = ("\n".join(per_lines)) if per_lines else "(no trades)"
        thresh_block = ""
        if thresholds:
            tlines = [f"{k:<20} {v:.2f}" for k, v in sorted(thresholds.items())]
            thresh_block = f"\n*Adaptive thresholds:*\n```\n" + "\n".join(tlines) + "\n```"
        self.send(
            f"📈 *J-Dawg stats*\n"
            f"```\n{body}\n```\n"
            f"*Per symbol:*\n```\n{per_block}\n```\n"
            f"*Bayesian:*\n```\n{bayes_summary}\n```"
            f"{thresh_block}"
        )

    # ---------- inbound polling ----------
    def poll_commands(self) -> list[str]:
        """
        Returns the list of NEW message texts since last call (only from configured chat).
        Non-blocking: uses short timeout so it slots into the main loop cleanly.
        """
        try:
            r = requests.get(
                f"{self.base}/getUpdates",
                params={"offset": self._last_update_id + 1, "timeout": 0},
                timeout=5,
            )
            if r.status_code != 200:
                return []
            data = r.json()
            if not data.get("ok"):
                return []
            texts: list[str] = []
            for upd in data.get("result", []):
                self._last_update_id = max(self._last_update_id, upd["update_id"])
                msg = upd.get("message") or upd.get("edited_message") or {}
                chat = msg.get("chat", {})
                if str(chat.get("id")) != self.chat_id:
                    continue
                text = msg.get("text")
                if text:
                    texts.append(text.strip())
            return texts
        except Exception as e:
            print(f"[telegram] poll error: {e}")
            return []
