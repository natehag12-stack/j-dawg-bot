"""
Telegram notifier — zero deps beyond `requests`.

PolyWRLD-style emoji-rich messages.
"""
from __future__ import annotations
import requests


def _money(x: float) -> str:
    sign = "-" if x < 0 else ""
    return f"{sign}${abs(x):,.2f}"


def _signed_money(x: float) -> str:
    sign = "+" if x >= 0 else "-"
    return f"{sign}${abs(x):,.2f}"


def _price(x: float) -> str:
    return f"{x:,.2f}"


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
    def send_startup(self, symbols, balance: float, adaptive: bool) -> None:
        symstr = " · ".join(symbols) if isinstance(symbols, (list, tuple)) else symbols
        self.send(
            "🤖 *J-Dawg Bot — ONLINE*\n\n"
            f"📊 Mode: Paper trading\n"
            f"👁️ Watching: {symstr}\n"
            f"💰 Balance: {_money(balance)}\n"
            f"🧠 Self-learning: {'ON' if adaptive else 'OFF'}"
        )

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
        risk_ticks: int,
        reward_ticks: int,
        units: float,
        risk_dollars: float,
        potential_dollars: float,
        rr: float,
        balance: float,
    ) -> None:
        side_emoji = "🟢" if side == "long" else "🔴"
        unit_label = "shares" if units >= 1 and abs(units - round(units)) < 0.01 else (
            "contracts" if units < 5 else "shares"
        )
        # Heuristic: futures show 2 decimal contracts; ETFs show whole shares
        if units >= 10:
            size_str = f"{units:,.0f} {unit_label}"
        else:
            size_str = f"{units:.2f} {unit_label}"

        self.send(
            "📄 *Paper Position Opened*\n\n"
            f"📊 *{symbol}* — {side_emoji} {side.upper()} @ {_price(entry)}\n"
            f"➡️ Direction: {side.upper()}  |  Confidence: {confidence*100:.1f}%\n"
            f"💵 Risk: {_money(risk_dollars)}  |  Size: {size_str}\n"
            f"🏆 Potential: {_signed_money(potential_dollars)} (RR 1:{rr:.2f})\n"
            f"🛑 Stop: {_price(stop)} (-{risk_ticks} ticks)\n"
            f"🎯 Target: {_price(target)} (+{reward_ticks} ticks)\n"
            f"🧠 Setup: {reason}\n"
            f"📊 Bayes n={samples}\n"
            f"💰 Balance: {_money(balance)}"
        )

    def send_close(
        self,
        *,
        symbol: str,
        side: str,
        entry: float,
        exit_price: float,
        pnl_r: float,
        pnl_dollars: float,
        ticks: float,
        exit_reason: str,
        held: str,
        balance: float,
    ) -> None:
        won = pnl_dollars >= 0
        verdict = "WIN" if won else "LOSS"
        head_emoji = "📈" if won else "📉"
        result_emoji = "✅" if won else "❌"
        pnl_emoji = "🟢" if won else "🔴"

        self.send(
            f"{head_emoji} *Paper Position Closed — {verdict}*\n\n"
            f"📊 *{symbol}* {side.upper()}\n"
            f"💵 Entry: {_price(entry)}  →  Exit: {_price(exit_price)}\n"
            f"{result_emoji} Result: {ticks:+.0f} ticks ({pnl_r:+.2f}R)\n"
            f"{pnl_emoji} P&L: {_signed_money(pnl_dollars)}\n"
            f"⏱️ Held: {held}\n"
            f"🎯 Reason: {exit_reason}\n"
            f"💰 Balance: {_money(balance)}"
        )

    def send_daily_summary(
        self,
        date_str: str,
        rows: list,
        per_symbol: dict,
        net_dollars: float,
        balance: float,
    ) -> None:
        wins = sum(1 for r in rows if r["outcome"] == "win")
        losses = sum(1 for r in rows if r["outcome"] == "loss")
        n = wins + losses
        wr = (wins / n * 100) if n else 0.0
        pnl_emoji = "🟢" if net_dollars >= 0 else "🔴"

        if n == 0:
            self.send(
                f"📅 *Daily Recap — {date_str}*\n\n"
                f"😴 No trades closed today.\n"
                f"💰 Balance: {_money(balance)}"
            )
            return

        per_lines = []
        for sym, s in per_symbol.items():
            sn = s["wins"] + s["losses"]
            if sn == 0:
                continue
            swr = (s["wins"] / sn * 100) if sn else 0.0
            per_lines.append(f"{sym}: {sn}t · {swr:.0f}% · {_signed_money(s.get('pnl_dollars', 0.0))}")
        per_block = ("\n📊 By symbol:\n" + "\n".join(per_lines)) if per_lines else ""

        self.send(
            f"📅 *Daily Recap — {date_str}*\n\n"
            f"✅ Wins: {wins}\n"
            f"❌ Losses: {losses}\n"
            f"🎯 Win rate: {wr:.1f}% ({n} trades)\n"
            f"{pnl_emoji} Net P&L: {_signed_money(net_dollars)}\n"
            f"💰 Balance: {_money(balance)}"
            f"{per_block}"
        )

    def send_stats(
        self,
        overall: dict,
        per_symbol: dict,
        balance: float,
        starting: float,
        roi_pct: float,
        bayes_lines: list[str] | None = None,
        thresholds: dict | None = None,
    ) -> None:
        wins = overall["wins"]
        losses = overall["losses"]
        pending = overall["pending"]
        n = wins + losses
        wr = (wins / n * 100) if n else 0.0
        net_dollars = balance - starting
        pnl_emoji = "🟢" if net_dollars >= 0 else "🔴"
        roi_emoji = "📈" if roi_pct >= 0 else "📉"

        per_lines = []
        for sym, s in per_symbol.items():
            sn = s["wins"] + s["losses"]
            if sn == 0 and not s["pending"]:
                continue
            swr = (s["wins"] / sn * 100) if sn else 0.0
            per_lines.append(f"{sym}: {sn}t · {swr:.0f}% · {_signed_money(s.get('pnl_dollars', 0.0))}")
        per_block = ("\n📊 By symbol:\n" + "\n".join(per_lines)) if per_lines else ""

        bayes_block = ""
        if bayes_lines:
            bayes_block = "\n\n🧠 Bayesian win-rate:\n" + "\n".join(bayes_lines)

        thresh_block = ""
        if thresholds:
            tlines = [f"{k}: {v:.2f}" for k, v in sorted(thresholds.items())]
            if tlines:
                thresh_block = "\n\n⚙️ Adaptive thresholds:\n" + "\n".join(tlines)

        body = (
            "📊 *J-Dawg P&L Report*\n\n"
            f"✅ Wins: {wins}\n"
            f"❌ Losses: {losses}\n"
            f"⏳ Open: {pending}\n"
            f"🎯 Win rate: {wr:.1f}% ({n} resolved)\n"
            f"{pnl_emoji} Net P&L: {_signed_money(net_dollars)}\n"
            f"💰 Balance: {_money(balance)}\n"
            f"{roi_emoji} ROI: {roi_pct:+.2f}% (start {_money(starting)})"
            f"{per_block}"
            f"{bayes_block}"
            f"{thresh_block}"
        )
        self.send(body)

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
