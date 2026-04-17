"""
Telegram notifier — zero deps beyond `requests`.

Also supports lightweight polling for inbound commands like /status.
"""
from __future__ import annotations
import requests


def _fmt_price(p: float) -> str:
    return f"{p:,.2f}"


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = str(chat_id)
        self.base = f"https://api.telegram.org/bot{token}"
        self._last_update_id = 0

    # ---------- low-level ----------
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

    # ---------- outbound ----------
    def send_startup(self, symbols, _bayes_summary: str = "") -> None:
        symstr = " · ".join(symbols) if isinstance(symbols, (list, tuple)) else symbols
        self.send(f"🤖 *J-Dawg online*\nWatching {symstr}")

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
        dot = "🟢" if side == "long" else "🔴"
        risk = abs(entry - stop)
        reward = abs(target - entry)
        rr = reward / risk if risk else 0.0
        risk_t = int(round(risk / tick_size)) if tick_size else 0
        rew_t = int(round(reward / tick_size)) if tick_size else 0
        verb = "paper opened" if paper else "signal"

        msg = (
            f"{dot} *{side.upper()} {symbol}* — {verb}\n"
            f"```\n"
            f"Entry   {_fmt_price(entry)}\n"
            f"Stop    {_fmt_price(stop)}   −{risk_t}t\n"
            f"Target  {_fmt_price(target)}  +{rew_t}t\n"
            f"RR      1:{rr:.2f}\n"
            f"```\n"
            f"Confidence {confidence*100:.0f}%  (n={samples})\n"
            f"_{reason}_"
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
        dot = "✅" if won else "❌"
        verdict = "WIN" if won else "LOSS"

        msg = (
            f"{dot} *{symbol} {side.upper()} closed — {verdict}*\n"
            f"```\n"
            f"{_fmt_price(entry)} → {_fmt_price(exit_price)}\n"
            f"{ticks:+.0f} ticks   ({pnl_r:+.2f}R)\n"
            f"held {held}  ·  {exit_reason}\n"
            f"```"
        )
        self.send(msg)

    def send_daily_summary(self, date_str: str, rows: list, per_symbol: dict) -> None:
        wins = sum(1 for r in rows if r["outcome"] == "win")
        losses = sum(1 for r in rows if r["outcome"] == "loss")
        n = wins + losses
        wr = (wins / n * 100) if n else 0.0
        net = sum((r["pnl_r"] or 0.0) for r in rows)

        if n == 0:
            self.send(f"📅 *Daily recap · {date_str}*\nNo trades closed today.")
            return

        per_lines = []
        for sym, s in per_symbol.items():
            sn = s["wins"] + s["losses"]
            swr = (s["wins"] / sn * 100) if sn else 0.0
            per_lines.append(f"{sym:<6} {sn:>2}t   {swr:4.0f}%   {s['pnl_r']:+.2f}R")

        msg = (
            f"📅 *Daily recap · {date_str}*\n"
            f"{n}t · {wins}W / {losses}L · {wr:.0f}%  ·  net {net:+.2f}R\n"
            f"```\n" + "\n".join(per_lines) + "\n```"
        )
        self.send(msg)

    def send_stats(
        self,
        overall: dict,
        per_symbol: dict,
        bayes_lines: list[str] | None = None,
        thresholds: dict | None = None,
    ) -> None:
        n = overall["wins"] + overall["losses"]
        wr = (overall["wins"] / n * 100) if n else 0.0
        net = overall["total_r"]

        if n == 0:
            head = "no closed trades yet"
        else:
            head = f"{n}t · {overall['wins']}W / {overall['losses']}L · {wr:.0f}%  ·  net {net:+.2f}R"
        pending = overall["pending"]
        head += f"\n_{pending} open_" if pending else ""

        per_block = ""
        per_lines = []
        for sym, s in per_symbol.items():
            sn = s["wins"] + s["losses"]
            if sn == 0 and not s["pending"]:
                continue
            swr = (s["wins"] / sn * 100) if sn else 0.0
            per_lines.append(f"{sym:<6} {sn:>2}t   {swr:4.0f}%   {s['total_r']:+.2f}R")
        if per_lines:
            per_block = "\n*By symbol*\n```\n" + "\n".join(per_lines) + "\n```"

        bayes_block = ""
        if bayes_lines:
            bayes_block = "\n*Bayesian win-rate*\n```\n" + "\n".join(bayes_lines) + "\n```"

        thresh_block = ""
        if thresholds:
            tlines = [f"{k:<14} {v:.2f}" for k, v in sorted(thresholds.items())]
            if tlines:
                thresh_block = "\n*Adaptive thresholds*\n```\n" + "\n".join(tlines) + "\n```"

        msg = f"📊 *J-Dawg stats*\n{head}{per_block}{bayes_block}{thresh_block}"
        self.send(msg)

    # ---------- inbound polling ----------
    def poll_commands(self) -> list[str]:
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
