from __future__ import annotations

import dataclasses
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from bitsentry.audit_engine import AuditEngine
from bitsentry.bgc_client import BGCClient, BGCError
from bitsentry.position_monitor import PositionMonitor
from bitsentry.risk_guardian import RiskGuardian
from bitsentry.strategy_evaluator import StrategyEvaluator

# ── Global component references ───────────────────────────────────────────────
# Populated during lifespan startup; all routes read from here.

_state: dict[str, Any] = {}


def _asdict(obj: Any) -> Any:
    """Recursively convert dataclasses to dicts for JSON serialization."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _asdict(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, list):
        return [_asdict(i) for i in obj]
    return obj


# ── Startup / shutdown ────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(application: FastAPI):
    demo = os.environ.get("BITGET_DEMO", "true").lower() != "false"
    try:
        client = BGCClient(demo=demo)
    except BGCError as exc:
        print(f"[bitsentry] WARNING: BGCClient failed to initialize: {exc}")
        client = None

    audit = AuditEngine()
    guardian = RiskGuardian(audit_engine=audit)
    monitor = PositionMonitor(bgc_client=client, audit_engine=audit) if client else None
    evaluator = StrategyEvaluator(audit_engine=audit)

    _state.update({
        "client": client,
        "audit": audit,
        "guardian": guardian,
        "monitor": monitor,
        "evaluator": evaluator,
        "demo": demo,
    })

    print("[bitsentry] API server ready.")
    yield
    _state.clear()


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="BitSentry API",
    description="Safety, audit, and intelligence layer for Bitget trading agents",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request / response models ─────────────────────────────────────────────────

class RiskCheckRequest(BaseModel):
    symbol: str
    side: str
    size_usdt: float
    leverage: float
    account_balance_usdt: float
    daily_pnl_usdt: float
    consecutive_losses: int


class RecordTradeRequest(BaseModel):
    strategy_tag: str
    symbol: str
    side: str
    entry_price: float
    exit_price: float
    size_usdt: float
    market_condition: str = ""


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {
        "name": "BitSentry",
        "version": "0.1.0",
        "docs": "/docs",
        "status": "running",
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "version": "0.1.0",
        "demo_mode": _state.get("demo", True),
    }


# ── Positions ─────────────────────────────────────────────────────────────────

@app.get("/positions")
def get_positions():
    monitor: PositionMonitor | None = _state.get("monitor")
    if not monitor:
        raise HTTPException(503, "PositionMonitor unavailable — BGCClient failed to initialize")
    return _asdict(monitor.get_positions())


@app.get("/positions/summary")
def get_positions_summary():
    monitor: PositionMonitor | None = _state.get("monitor")
    if not monitor:
        raise HTTPException(503, "PositionMonitor unavailable — BGCClient failed to initialize")
    return monitor.get_account_summary()


@app.get("/positions/safe-to-trade")
def safe_to_trade(
    symbol: str = Query(..., description="Trading symbol, e.g. BTCUSDT"),
    direction: str = Query(..., description="long or short"),
):
    monitor: PositionMonitor | None = _state.get("monitor")
    if not monitor:
        raise HTTPException(503, "PositionMonitor unavailable — BGCClient failed to initialize")
    return monitor.get_safe_to_trade(symbol=symbol, direction=direction)


# ── Risk ──────────────────────────────────────────────────────────────────────

@app.post("/risk/check")
def risk_check(body: RiskCheckRequest):
    guardian: RiskGuardian = _state["guardian"]
    result = guardian.check(
        symbol=body.symbol,
        side=body.side,
        size_usdt=body.size_usdt,
        leverage=body.leverage,
        account_balance_usdt=body.account_balance_usdt,
        daily_pnl_usdt=body.daily_pnl_usdt,
        consecutive_losses=body.consecutive_losses,
    )
    return _asdict(result)


# ── Strategy — leaderboard must be declared before /{strategy_tag} ────────────

@app.get("/strategy/leaderboard")
def strategy_leaderboard():
    evaluator: StrategyEvaluator = _state["evaluator"]
    return evaluator.get_leaderboard()


@app.get("/strategy/{strategy_tag}")
def strategy_health(strategy_tag: str):
    evaluator: StrategyEvaluator = _state["evaluator"]
    health = evaluator.evaluate(strategy_tag)
    return _asdict(health)


@app.post("/strategy/record")
def record_trade(body: RecordTradeRequest):
    evaluator: StrategyEvaluator = _state["evaluator"]
    trade_id = evaluator.record_trade_result(
        strategy_tag=body.strategy_tag,
        symbol=body.symbol,
        side=body.side,
        entry_price=body.entry_price,
        exit_price=body.exit_price,
        size_usdt=body.size_usdt,
        market_condition=body.market_condition,
    )
    return {"recorded": True, "trade_id": trade_id}


# ── Audit ─────────────────────────────────────────────────────────────────────

@app.get("/audit/report")
def audit_report():
    audit: AuditEngine = _state["audit"]
    return audit.generate_audit_report()


@app.get("/audit/verify")
def audit_verify():
    audit: AuditEngine = _state["audit"]
    report = audit.generate_audit_report()
    integrity_hash = report["integrity_hash"]
    verified = audit.verify_integrity(integrity_hash)
    return {"verified": verified, "hash": integrity_hash}
