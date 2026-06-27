from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

try:
    import requests as _requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

if TYPE_CHECKING:
    from bitsentry.audit_engine import AuditEngine
    from bitsentry.position_monitor import PositionMonitor
    from bitsentry.strategy_evaluator import StrategyEvaluator

_VERSION = "0.2.0"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _date_str(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


class ReportGenerator:
    """
    Generates daily/weekly/monthly plain-text reports and optionally
    sends them to a Telegram chat.
    """

    def __init__(
        self,
        audit_engine: "AuditEngine",
        strategy_evaluator: "StrategyEvaluator",
        position_monitor: "PositionMonitor | None" = None,
        telegram_token: str | None = None,
        telegram_chat_id: str | None = None,
    ):
        self._audit     = audit_engine
        self._evaluator = strategy_evaluator
        self._monitor   = position_monitor

        self._token   = telegram_token   or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self._chat_id = telegram_chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")

    # ── Internal data helpers ─────────────────────────────────────────────────

    def _audit_data(self) -> dict:
        return self._audit.generate_audit_report()

    def _position_data(self) -> dict:
        if not self._monitor:
            return {
                "total_positions": 0,
                "green_count": 0,
                "yellow_count": 0,
                "red_count": 0,
                "overall_safety": "UNKNOWN",
                "total_unrealized_pnl": 0.0,
            }
        return self._monitor.get_account_summary()

    def _strategy_data(self) -> list[dict]:
        return self._evaluator.get_leaderboard()

    # ── Section builders ──────────────────────────────────────────────────────

    def _section_risk(self, report: dict) -> str:
        total    = report["total_trade_intents"]
        approved = round(total * report["approval_rate"] / 100) if total else 0
        blocked  = total - approved

        lines = [
            "📋 *RISK ACTIVITY*",
            f"  Total trade intents : {total}",
            f"  Approved            : {approved}",
            f"  Blocked             : {blocked}",
        ]
        if blocked and total:
            lines.append(f"  Approval rate       : {report['approval_rate']}%")
        return "\n".join(lines)

    def _section_positions(self, pos: dict) -> str:
        pnl = pos["total_unrealized_pnl"]
        pnl_str = ("+$" if pnl >= 0 else "-$") + f"{abs(pnl):.2f}"
        safety_icon = {"GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴"}.get(
            pos["overall_safety"], "⚪"
        )

        return "\n".join([
            "📍 *POSITION SAFETY*",
            f"  Overall safety : {safety_icon} {pos['overall_safety']}",
            f"  Positions      : 🟢 {pos['green_count']}  🟡 {pos['yellow_count']}  🔴 {pos['red_count']}",
            f"  Unrealized PnL : {pnl_str} USDT",
        ])

    def _section_strategies(self, strategies: list[dict]) -> str:
        if not strategies:
            return "📈 *STRATEGY PERFORMANCE*\n  No strategies recorded yet."

        verdict_icon = {
            "PERFORMING":        "✅",
            "DEGRADING":         "⚠️",
            "DEAD":              "💀",
            "INSUFFICIENT_DATA": "❓",
        }

        lines = ["📈 *STRATEGY PERFORMANCE*"]
        best = strategies[0]
        dead_or_degrading = [s for s in strategies if s["verdict"] in ("DEAD", "DEGRADING")]

        for s in strategies:
            icon = verdict_icon.get(s["verdict"], "❓")
            lines.append(
                f"  {icon} `{s['strategy_tag']}`  "
                f"WR30d {s['win_rate_30d']:.0%}  "
                f"PF {s['profit_factor']:.2f}  "
                f"PnL ${s['total_pnl_usdt']:.2f}"
            )

        lines.append(f"  🏆 Best: `{best['strategy_tag']}` (PF {best['profit_factor']:.2f})")

        for s in dead_or_degrading:
            icon = verdict_icon[s["verdict"]]
            lines.append(f"  {icon} WARNING: `{s['strategy_tag']}` is {s['verdict']}")

        return "\n".join(lines)

    def _section_audit(self, report: dict) -> str:
        h = report["integrity_hash"]
        short_hash = h[:16] + "..." if h else "—"
        verified = self._audit.verify_integrity(h) if h else False
        integrity_str = "✅ Verified" if verified else "❌ Tampered"

        return "\n".join([
            "🔒 *AUDIT INTEGRITY*",
            f"  SHA-256 : `{short_hash}`",
            f"  Status  : {integrity_str}",
            f"  Checks  : {report['total_risk_checks']} risk checks logged",
        ])

    def _build_report(self, period_label: str, date_range: str) -> str:
        # strategies first — evaluate() writes checkpoints to the DB
        # audit hash must be taken AFTER those writes to stay consistent
        strategies = self._strategy_data()
        pos        = self._position_data()
        report     = self._audit_data()

        divider = "─" * 36

        return "\n".join([
            f"📊 *BitSentry {period_label} Report — {date_range}*",
            divider,
            self._section_risk(report),
            divider,
            self._section_positions(pos),
            divider,
            self._section_strategies(strategies),
            divider,
            self._section_audit(report),
            divider,
            f"_Generated by BitSentry v{_VERSION}_",
        ])

    # ── Public report methods ─────────────────────────────────────────────────

    def generate_daily_report(self) -> str:
        today = _date_str(_now_utc())
        return self._build_report("Daily", today)

    def generate_weekly_report(self) -> str:
        end   = _now_utc()
        start = end - timedelta(days=7)
        span  = f"{_date_str(start)} → {_date_str(end)}"
        return self._build_report("Weekly", span)

    def generate_monthly_report(self) -> str:
        end   = _now_utc()
        start = end - timedelta(days=30)
        span  = f"{_date_str(start)} → {_date_str(end)}"
        return self._build_report("Monthly", span)

    # ── Telegram ──────────────────────────────────────────────────────────────

    def send_telegram(self, message: str) -> bool:
        if not self._token or not self._chat_id:
            print("[bitsentry] No Telegram token configured — printing report to console:\n")
            print(message)
            return False

        if not _HAS_REQUESTS:
            print("[bitsentry] 'requests' not installed — cannot send Telegram message.")
            return False

        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        try:
            resp = _requests.post(
                url,
                json={
                    "chat_id":    self._chat_id,
                    "text":       message,
                    "parse_mode": "Markdown",
                },
                timeout=10,
            )
            if resp.status_code == 200:
                print(f"[bitsentry] Telegram report sent (chat_id={self._chat_id})")
                return True
            else:
                print(f"[bitsentry] Telegram send failed: {resp.status_code} {resp.text[:200]}")
                return False
        except Exception as exc:
            print(f"[bitsentry] Telegram send error: {exc}")
            return False

    def send_daily_report(self) -> bool:
        return self.send_telegram(self.generate_daily_report())

    def send_weekly_report(self) -> bool:
        return self.send_telegram(self.generate_weekly_report())

    def send_monthly_report(self) -> bool:
        return self.send_telegram(self.generate_monthly_report())
