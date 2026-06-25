from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bitsentry.audit_engine import AuditEngine


@dataclass
class StrategyHealth:
    strategy_tag: str
    total_trades: int
    win_rate_7d: float
    win_rate_30d: float
    total_pnl_usdt: float
    avg_win_usdt: float
    avg_loss_usdt: float
    profit_factor: float
    best_market_condition: str
    worst_market_condition: str
    verdict: str
    recommendation: str


_VERDICTS = {
    "PERFORMING":         "Strategy is performing well. Continue running with current parameters.",
    "DEGRADING":          "Win rate is declining. Consider reducing position size by 50% and reviewing entry signals.",
    "DEAD":               "Strategy win rate is critically low. STOP trading this strategy immediately and backtest from scratch.",
    "INSUFFICIENT_DATA":  "Not enough trades to evaluate. Need at least 3 trades for a meaningful verdict.",
}


class StrategyEvaluator:
    """
    Tracks per-strategy trade results and produces health verdicts.

    Data is stored in a `trade_results` table co-located in the
    AuditEngine's SQLite database so everything stays in one file.
    """

    def __init__(self, audit_engine: "AuditEngine"):
        self._audit = audit_engine
        self._db_path = audit_engine.db_path
        self._ensure_table()

    # ── Schema ───────────────────────────────────────────────────────────────

    def _ensure_table(self) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trade_results (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp        TEXT NOT NULL,
                    strategy_tag     TEXT NOT NULL,
                    symbol           TEXT NOT NULL,
                    side             TEXT NOT NULL,
                    entry_price      REAL NOT NULL,
                    exit_price       REAL NOT NULL,
                    size_usdt        REAL NOT NULL,
                    pnl_usdt         REAL NOT NULL,
                    pnl_pct          REAL NOT NULL,
                    outcome          TEXT NOT NULL,
                    market_condition TEXT NOT NULL DEFAULT ''
                )
            """)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    # ── PnL helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _calc_pnl(
        side: str, entry_price: float, exit_price: float, size_usdt: float
    ) -> tuple[float, float]:
        """Return (pnl_usdt, pnl_pct) for a closed trade."""
        if entry_price <= 0:
            return 0.0, 0.0
        if side.lower() in ("buy", "long"):
            pnl_pct = (exit_price - entry_price) / entry_price
        else:
            pnl_pct = (entry_price - exit_price) / entry_price
        pnl_usdt = pnl_pct * size_usdt
        return round(pnl_usdt, 6), round(pnl_pct, 8)

    # ── Write ────────────────────────────────────────────────────────────────

    def record_trade_result(
        self,
        strategy_tag: str,
        symbol: str,
        side: str,
        entry_price: float,
        exit_price: float,
        size_usdt: float,
        market_condition: str = "",
    ) -> int:
        pnl_usdt, pnl_pct = self._calc_pnl(side, entry_price, exit_price, size_usdt)
        outcome = "WIN" if pnl_usdt > 0 else "LOSS"

        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO trade_results
                    (timestamp, strategy_tag, symbol, side, entry_price,
                     exit_price, size_usdt, pnl_usdt, pnl_pct, outcome, market_condition)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self._now(), strategy_tag, symbol, side,
                    entry_price, exit_price, size_usdt,
                    pnl_usdt, pnl_pct, outcome, market_condition,
                ),
            )
            return cur.lastrowid

    # ── Evaluation helpers ───────────────────────────────────────────────────

    @staticmethod
    def _win_rate(rows: list[dict]) -> float:
        if not rows:
            return 0.0
        wins = sum(1 for r in rows if r["outcome"] == "WIN")
        return wins / len(rows)

    @staticmethod
    def _cutoff(days: int) -> str:
        dt = datetime.now(timezone.utc) - timedelta(days=days)
        return dt.isoformat()

    def _rows_for(self, strategy_tag: str) -> list[dict]:
        with self._conn() as conn:
            return [
                dict(r) for r in conn.execute(
                    "SELECT * FROM trade_results WHERE strategy_tag = ? ORDER BY id",
                    (strategy_tag,),
                )
            ]

    def _rows_since(self, strategy_tag: str, days: int) -> list[dict]:
        cutoff = self._cutoff(days)
        with self._conn() as conn:
            return [
                dict(r) for r in conn.execute(
                    """SELECT * FROM trade_results
                       WHERE strategy_tag = ? AND timestamp >= ?
                       ORDER BY id""",
                    (strategy_tag, cutoff),
                )
            ]

    @staticmethod
    def _best_worst_condition(rows: list[dict]) -> tuple[str, str]:
        """Return (best_condition, worst_condition) by win rate."""
        conditions: dict[str, list[str]] = {}
        for r in rows:
            cond = r.get("market_condition") or "unknown"
            conditions.setdefault(cond, []).append(r["outcome"])

        if not conditions:
            return "unknown", "unknown"

        rates = {
            cond: outcomes.count("WIN") / len(outcomes)
            for cond, outcomes in conditions.items()
        }
        best = max(rates, key=lambda k: rates[k])
        worst = min(rates, key=lambda k: rates[k])
        return best, worst

    @staticmethod
    def _verdict(
        total_trades: int,
        win_rate_7d: float,
        win_rate_30d: float,
        profit_factor: float,
    ) -> str:
        if total_trades < 3:
            return "INSUFFICIENT_DATA"
        if win_rate_7d < 0.35:
            return "DEAD"
        if win_rate_30d >= 0.55 and profit_factor >= 1.2:
            return "PERFORMING"
        return "DEGRADING"

    # ── Public API ───────────────────────────────────────────────────────────

    def evaluate(self, strategy_tag: str) -> StrategyHealth:
        all_rows = self._rows_for(strategy_tag)
        rows_7d = self._rows_since(strategy_tag, 7)
        rows_30d = self._rows_since(strategy_tag, 30)

        total_trades = len(all_rows)
        win_rate_7d = self._win_rate(rows_7d)
        win_rate_30d = self._win_rate(rows_30d)

        total_pnl = sum(r["pnl_usdt"] for r in all_rows)

        wins = [r["pnl_usdt"] for r in all_rows if r["outcome"] == "WIN"]
        losses = [r["pnl_usdt"] for r in all_rows if r["outcome"] == "LOSS"]

        avg_win = (sum(wins) / len(wins)) if wins else 0.0
        avg_loss = (sum(losses) / len(losses)) if losses else 0.0

        total_wins = sum(wins)
        total_losses = abs(sum(losses))
        profit_factor = (total_wins / total_losses) if total_losses > 0 else 0.0

        best_cond, worst_cond = self._best_worst_condition(all_rows)

        v = self._verdict(total_trades, win_rate_7d, win_rate_30d, profit_factor)

        # Log checkpoint to AuditEngine
        self._audit.log_strategy_checkpoint(
            strategy_tag=strategy_tag,
            win_rate_7d=round(win_rate_7d, 4),
            win_rate_30d=round(win_rate_30d, 4),
            total_trades=total_trades,
            verdict=v,
            market_condition=best_cond,
        )

        return StrategyHealth(
            strategy_tag=strategy_tag,
            total_trades=total_trades,
            win_rate_7d=round(win_rate_7d, 4),
            win_rate_30d=round(win_rate_30d, 4),
            total_pnl_usdt=round(total_pnl, 4),
            avg_win_usdt=round(avg_win, 4),
            avg_loss_usdt=round(avg_loss, 4),
            profit_factor=round(profit_factor, 4),
            best_market_condition=best_cond,
            worst_market_condition=worst_cond,
            verdict=v,
            recommendation=_VERDICTS[v],
        )

    def evaluate_all(self) -> list[StrategyHealth]:
        with self._conn() as conn:
            tags = [
                row[0] for row in conn.execute(
                    "SELECT DISTINCT strategy_tag FROM trade_results ORDER BY strategy_tag"
                )
            ]
        return [self.evaluate(tag) for tag in tags]

    def get_leaderboard(self) -> list[dict]:
        healths = self.evaluate_all()
        ranked = sorted(healths, key=lambda h: h.profit_factor, reverse=True)
        return [
            {
                "strategy_tag":    h.strategy_tag,
                "profit_factor":   h.profit_factor,
                "win_rate_30d":    h.win_rate_30d,
                "total_pnl_usdt":  h.total_pnl_usdt,
                "total_trades":    h.total_trades,
                "verdict":         h.verdict,
            }
            for h in ranked
        ]
