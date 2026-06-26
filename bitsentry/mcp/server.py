"""
BitSentry MCP Server

Exposes BitSentry risk, position, strategy, and audit tools
so any MCP-compatible agent (Claude, Agent Hub, etc.) can call them.

Run standalone:
  python -m bitsentry.mcp.server

Or as stdio MCP server (Claude Code config):
  command: /opt/miniconda3/bin/python3.13 -m bitsentry.mcp.server
"""
from __future__ import annotations

import dataclasses
import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from bitsentry.audit_engine import AuditEngine
from bitsentry.bgc_client import BGCClient, BGCError
from bitsentry.position_monitor import PositionMonitor
from bitsentry.risk_guardian import RiskGuardian
from bitsentry.strategy_evaluator import StrategyEvaluator

# ── Server instance ───────────────────────────────────────────────────────────

mcp = FastMCP("bitsentry")

# ── Component initialisation ──────────────────────────────────────────────────

demo = os.environ.get("BITGET_DEMO_MODE", "true").lower() == "true"

try:
    _client = BGCClient(demo=demo)
except BGCError as exc:
    print(f"[bitsentry-mcp] WARNING: BGCClient init failed: {exc}")
    _client = None

_audit    = AuditEngine()
_guardian = RiskGuardian(audit_engine=_audit)
_monitor  = PositionMonitor(bgc_client=_client, audit_engine=_audit) if _client else None
_evaluator = StrategyEvaluator(audit_engine=_audit)

print(f"[bitsentry-mcp] Ready. demo={demo}, bgc={'ok' if _client else 'unavailable'}")

# ── Helpers ───────────────────────────────────────────────────────────────────

def _asdict(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _asdict(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, list):
        return [_asdict(i) for i in obj]
    return obj


# ── Tool 1: check_risk ────────────────────────────────────────────────────────

@mcp.tool()
def check_risk(
    symbol: str,
    side: str,
    size_usdt: float,
    leverage: float,
    account_balance_usdt: float,
    daily_pnl_usdt: float,
    consecutive_losses: int,
) -> dict:
    """Check if a trade is safe to execute according to BitSentry risk rules.
    Call this before placing any order on Bitget."""
    result = _guardian.check(
        symbol=symbol,
        side=side,
        size_usdt=size_usdt,
        leverage=leverage,
        account_balance_usdt=account_balance_usdt,
        daily_pnl_usdt=daily_pnl_usdt,
        consecutive_losses=consecutive_losses,
    )
    return _asdict(result)


# ── Tool 2: get_position_safety ───────────────────────────────────────────────

@mcp.tool()
def get_position_safety() -> dict:
    """Get safety ratings for all open Bitget positions.
    Returns GREEN/YELLOW/RED rating for each position."""
    if not _monitor:
        return {"error": "PositionMonitor unavailable — BGCClient failed to initialize"}
    positions = _monitor.get_positions()
    summary   = _monitor.get_account_summary()
    return {
        "overall_safety": summary["overall_safety"],
        "positions": _asdict(positions),
    }


# ── Tool 3: get_account_summary ───────────────────────────────────────────────

@mcp.tool()
def get_account_summary() -> dict:
    """Get a summary of current account safety status including position counts
    by safety rating."""
    if not _monitor:
        return {"error": "PositionMonitor unavailable — BGCClient failed to initialize"}
    return _monitor.get_account_summary()


# ── Tool 4: evaluate_strategy ─────────────────────────────────────────────────

@mcp.tool()
def evaluate_strategy(strategy_tag: str) -> dict:
    """Evaluate the performance of a trading strategy.
    Returns PERFORMING, DEGRADING, or DEAD verdict."""
    health = _evaluator.evaluate(strategy_tag)
    return _asdict(health)


# ── Tool 5: record_trade ──────────────────────────────────────────────────────

@mcp.tool()
def record_trade(
    strategy_tag: str,
    symbol: str,
    side: str,
    entry_price: float,
    exit_price: float,
    size_usdt: float,
    market_condition: str = "",
) -> dict:
    """Record a completed trade result for strategy performance tracking."""
    trade_id = _evaluator.record_trade_result(
        strategy_tag=strategy_tag,
        symbol=symbol,
        side=side,
        entry_price=entry_price,
        exit_price=exit_price,
        size_usdt=size_usdt,
        market_condition=market_condition,
    )
    # Re-evaluate to get updated outcome in response
    pnl_usdt, _ = _evaluator._calc_pnl(side, entry_price, exit_price, size_usdt)
    outcome = "WIN" if pnl_usdt > 0 else "LOSS"
    health = _evaluator.evaluate(strategy_tag)
    return {
        "recorded": True,
        "trade_id": trade_id,
        "outcome": outcome,
        "pnl_usdt": round(pnl_usdt, 4),
        "strategy_verdict": health.verdict,
        "win_rate_30d": health.win_rate_30d,
    }


# ── Tool 6: get_audit_report ──────────────────────────────────────────────────

@mcp.tool()
def get_audit_report() -> dict:
    """Get the full BitSentry audit report with SHA-256 integrity hash.
    Use this to verify all trading decisions are logged."""
    return _audit.generate_audit_report()


# ── Entrypoint ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
