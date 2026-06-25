import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class AuditEngine:
    """
    Append-only audit trail stored in SQLite.
    Every record is hashed together for tamper detection.
    """

    def __init__(self, db_path: str = "bitsentry_audit.db"):
        self.db_path = db_path
        self._init_db()

    # ── Schema ──────────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS trade_intents (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp       TEXT NOT NULL,
                    symbol          TEXT NOT NULL,
                    side            TEXT NOT NULL,
                    size            REAL NOT NULL,
                    leverage        REAL NOT NULL,
                    signal_source   TEXT NOT NULL,
                    reasoning       TEXT,
                    approved        INTEGER NOT NULL DEFAULT 0,
                    block_reason    TEXT
                );

                CREATE TABLE IF NOT EXISTS risk_checks (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp       TEXT NOT NULL,
                    symbol          TEXT NOT NULL,
                    layer_name      TEXT NOT NULL,
                    passed          INTEGER NOT NULL,
                    reason          TEXT,
                    value_checked   REAL,
                    threshold       REAL
                );

                CREATE TABLE IF NOT EXISTS strategy_checkpoints (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp       TEXT NOT NULL,
                    strategy_tag    TEXT NOT NULL,
                    win_rate_7d     REAL,
                    win_rate_30d    REAL,
                    total_trades    INTEGER,
                    verdict         TEXT,
                    market_condition TEXT
                );
            """)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    # ── Write methods ────────────────────────────────────────────────────────

    def log_trade_intent(
        self,
        symbol: str,
        side: str,
        size: float,
        leverage: float,
        signal_source: str,
        reasoning: str = "",
        approved: bool = False,
        block_reason: str | None = None,
    ) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO trade_intents
                    (timestamp, symbol, side, size, leverage, signal_source,
                     reasoning, approved, block_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self._now(), symbol, side, size, leverage,
                    signal_source, reasoning, int(approved), block_reason,
                ),
            )
            return cur.lastrowid

    def log_risk_check(
        self,
        symbol: str,
        layer_name: str,
        passed: bool,
        reason: str = "",
        value_checked: float | None = None,
        threshold: float | None = None,
    ) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO risk_checks
                    (timestamp, symbol, layer_name, passed, reason,
                     value_checked, threshold)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self._now(), symbol, layer_name, int(passed),
                    reason, value_checked, threshold,
                ),
            )
            return cur.lastrowid

    def log_strategy_checkpoint(
        self,
        strategy_tag: str,
        win_rate_7d: float,
        win_rate_30d: float,
        total_trades: int,
        verdict: str,
        market_condition: str = "",
    ) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO strategy_checkpoints
                    (timestamp, strategy_tag, win_rate_7d, win_rate_30d,
                     total_trades, verdict, market_condition)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self._now(), strategy_tag, win_rate_7d, win_rate_30d,
                    total_trades, verdict, market_condition,
                ),
            )
            return cur.lastrowid

    # ── Integrity hash ───────────────────────────────────────────────────────

    def _all_records_canonical(self) -> str:
        """Return a deterministic JSON string over all rows in all tables."""
        with self._conn() as conn:
            intents = [dict(r) for r in conn.execute(
                "SELECT * FROM trade_intents ORDER BY id"
            )]
            checks = [dict(r) for r in conn.execute(
                "SELECT * FROM risk_checks ORDER BY id"
            )]
            checkpoints = [dict(r) for r in conn.execute(
                "SELECT * FROM strategy_checkpoints ORDER BY id"
            )]

        payload = {
            "trade_intents": intents,
            "risk_checks": checks,
            "strategy_checkpoints": checkpoints,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    def _compute_hash(self) -> str:
        canonical = self._all_records_canonical()
        return hashlib.sha256(canonical.encode()).hexdigest()

    # ── Report ───────────────────────────────────────────────────────────────

    def generate_audit_report(self) -> dict:
        with self._conn() as conn:
            total_intents = conn.execute(
                "SELECT COUNT(*) FROM trade_intents"
            ).fetchone()[0]
            total_checks = conn.execute(
                "SELECT COUNT(*) FROM risk_checks"
            ).fetchone()[0]
            approved_count = conn.execute(
                "SELECT COUNT(*) FROM trade_intents WHERE approved = 1"
            ).fetchone()[0]

        approval_rate = (approved_count / total_intents * 100) if total_intents else 0.0
        rejection_rate = 100.0 - approval_rate if total_intents else 0.0

        return {
            "total_trade_intents": total_intents,
            "total_risk_checks": total_checks,
            "approval_rate": round(approval_rate, 2),
            "rejection_rate": round(rejection_rate, 2),
            "integrity_hash": self._compute_hash(),
            "generated_at": self._now(),
        }

    def verify_integrity(self, expected_hash: str) -> bool:
        return self._compute_hash() == expected_hash

    # ── HTML export ──────────────────────────────────────────────────────────

    def export_html_report(self, output_path: str = "validation/audit_report.html") -> None:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        report = self.generate_audit_report()

        with self._conn() as conn:
            intents = [dict(r) for r in conn.execute(
                "SELECT * FROM trade_intents ORDER BY id DESC"
            )]
            checks = [dict(r) for r in conn.execute(
                "SELECT * FROM risk_checks ORDER BY id DESC"
            )]
            checkpoints = [dict(r) for r in conn.execute(
                "SELECT * FROM strategy_checkpoints ORDER BY id DESC"
            )]

        def _rows(records: list[dict[str, Any]], cols: list[str]) -> str:
            if not records:
                return f"<tr><td colspan='{len(cols)}' class='empty'>No records</td></tr>"
            rows = []
            for rec in records:
                cells = "".join(f"<td>{_esc(rec.get(c, ''))}</td>" for c in cols)
                rows.append(f"<tr>{cells}</tr>")
            return "\n".join(rows)

        def _esc(val: Any) -> str:
            return str(val).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        def _thead(cols: list[str]) -> str:
            return "<tr>" + "".join(f"<th>{c}</th>" for c in cols) + "</tr>"

        intent_cols = [
            "id", "timestamp", "symbol", "side", "size", "leverage",
            "signal_source", "reasoning", "approved", "block_reason",
        ]
        check_cols = [
            "id", "timestamp", "symbol", "layer_name", "passed",
            "reason", "value_checked", "threshold",
        ]
        cp_cols = [
            "id", "timestamp", "strategy_tag", "win_rate_7d", "win_rate_30d",
            "total_trades", "verdict", "market_condition",
        ]

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BitSentry Audit Report</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: 'Segoe UI', system-ui, sans-serif; background: #0f1117; color: #e2e8f0; padding: 2rem; }}
  h1 {{ font-size: 1.8rem; color: #f97316; margin-bottom: 0.25rem; }}
  .subtitle {{ color: #64748b; font-size: 0.9rem; margin-bottom: 2rem; }}
  .integrity-box {{
    background: #1e293b; border: 2px solid #22c55e; border-radius: 8px;
    padding: 1.25rem 1.5rem; margin-bottom: 2rem;
  }}
  .integrity-box h2 {{ color: #22c55e; font-size: 1rem; margin-bottom: 0.5rem; }}
  .hash {{ font-family: monospace; font-size: 0.85rem; color: #86efac; word-break: break-all; }}
  .stats {{ display: flex; gap: 1rem; margin-bottom: 2rem; flex-wrap: wrap; }}
  .stat {{
    background: #1e293b; border-radius: 8px; padding: 1rem 1.5rem;
    flex: 1; min-width: 160px; text-align: center;
  }}
  .stat .val {{ font-size: 2rem; font-weight: 700; color: #f97316; }}
  .stat .lbl {{ font-size: 0.8rem; color: #64748b; margin-top: 0.25rem; }}
  h3 {{ color: #cbd5e1; font-size: 1.1rem; margin: 1.5rem 0 0.75rem; }}
  .table-wrap {{ overflow-x: auto; margin-bottom: 1rem; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.82rem; }}
  th {{ background: #1e293b; color: #94a3b8; padding: 0.6rem 0.75rem; text-align: left; white-space: nowrap; }}
  td {{ padding: 0.55rem 0.75rem; border-bottom: 1px solid #1e293b; vertical-align: top; }}
  tr:hover td {{ background: #1e293b44; }}
  .empty {{ color: #475569; text-align: center; padding: 1rem; }}
  .footer {{ margin-top: 2rem; color: #334155; font-size: 0.78rem; }}
</style>
</head>
<body>
<h1>BitSentry Audit Report</h1>
<p class="subtitle">Generated at {report["generated_at"]}</p>

<div class="integrity-box">
  <h2>SHA-256 Integrity Hash</h2>
  <div class="hash">{report["integrity_hash"]}</div>
</div>

<div class="stats">
  <div class="stat"><div class="val">{report["total_trade_intents"]}</div><div class="lbl">Trade Intents</div></div>
  <div class="stat"><div class="val">{report["total_risk_checks"]}</div><div class="lbl">Risk Checks</div></div>
  <div class="stat"><div class="val">{report["approval_rate"]}%</div><div class="lbl">Approval Rate</div></div>
  <div class="stat"><div class="val">{report["rejection_rate"]}%</div><div class="lbl">Rejection Rate</div></div>
</div>

<h3>Trade Intents</h3>
<div class="table-wrap">
<table>
<thead>{_thead(intent_cols)}</thead>
<tbody>{_rows(intents, intent_cols)}</tbody>
</table>
</div>

<h3>Risk Checks</h3>
<div class="table-wrap">
<table>
<thead>{_thead(check_cols)}</thead>
<tbody>{_rows(checks, check_cols)}</tbody>
</table>
</div>

<h3>Strategy Checkpoints</h3>
<div class="table-wrap">
<table>
<thead>{_thead(cp_cols)}</thead>
<tbody>{_rows(checkpoints, cp_cols)}</tbody>
</table>
</div>

<div class="footer">
  BitSentry &mdash; Safety &amp; Audit Layer for Bitget &mdash;
  Integrity hash covers all records in all three tables.
</div>
</body>
</html>"""

        Path(output_path).write_text(html, encoding="utf-8")
        print(f"[bitsentry] HTML report written to {output_path}")
