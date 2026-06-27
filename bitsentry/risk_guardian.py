from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from bitsentry.audit_engine import AuditEngine

_BGC_BIN: str = shutil.which("bgc") or "bgc"


@dataclass
class RiskCheckResult:
    approved: bool
    agent_instruction: str          # PROCEED | REDUCE_SIZE | REDUCE_SIZE_AND_LEVERAGE | WAIT | BLOCKED
    blocking_layer: str | None
    reason: str
    recommended_size_usdt: float
    recommended_leverage: int
    original_size_usdt: float
    original_leverage: int
    fear_greed_index: int
    fear_greed_label: str
    funding_rate: float
    volatility_24h: float
    risk_score: int
    warnings: list[str] = field(default_factory=list)
    size_adjustment_reason: str = ""
    leverage_adjustment_reason: str = ""


class RiskGuardian:
    """
    5-layer pre-trade risk middleware, extended with live market intelligence.

    Layers 1-4 are hard blocks.  Layer 5 is a warning-only throttle.
    After all layers pass, market conditions (Fear&Greed, funding rate,
    volatility) are fetched and used to produce agent-ready instructions
    and adjusted size / leverage recommendations.
    """

    def __init__(
        self,
        config_path: str = "config/risk_rules.yaml",
        audit_engine: "AuditEngine | None" = None,
    ):
        self._config_path = config_path
        self._rules = self._load_rules(config_path)
        self._audit = audit_engine
        print(
            f"[bitsentry] RiskGuardian loaded rules from {config_path}: "
            f"leverage_cap={self._rules['leverage_cap']}, "
            f"max_position_size_pct={self._rules['max_position_size_pct']}%, "
            f"daily_loss_limit_pct={self._rules['daily_loss_limit_pct']}%, "
            f"consecutive_loss_limit={self._rules['consecutive_loss_limit']}, "
            f"allowed_symbols={self._rules['allowed_symbols']}"
        )

    # ── Config I/O ───────────────────────────────────────────────────────────

    @staticmethod
    def _load_rules(config_path: str) -> dict[str, Any]:
        path = Path(config_path)
        if not path.exists():
            raise FileNotFoundError(
                f"Risk rules config not found: {config_path}. "
                "Expected YAML with a top-level 'risk_rules' key."
            )
        with path.open() as fh:
            raw = yaml.safe_load(fh)
        rules = raw.get("risk_rules", {})
        required = {
            "leverage_cap", "max_position_size_pct",
            "daily_loss_limit_pct", "consecutive_loss_limit",
            "allowed_symbols",
        }
        missing = required - rules.keys()
        if missing:
            raise ValueError(f"risk_rules.yaml is missing keys: {missing}")
        rules.setdefault("blocked_symbols", [])
        rules.setdefault("market_conditions", {})
        rules.setdefault("size_reduction_factor", 0.5)
        rules.setdefault("leverage_reduction_factor", 0.5)
        return rules

    def _save_rules(self) -> None:
        path = Path(self._config_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as fh:
            yaml.dump({"risk_rules": self._rules}, fh, default_flow_style=False,
                      allow_unicode=True, sort_keys=False)

    # ── Market intelligence fetchers (all silent-fail) ────────────────────────

    def fetch_fear_greed(self) -> dict:
        try:
            import requests
            resp = requests.get("https://api.alternative.me/fng/?limit=1", timeout=5)
            entry = resp.json()["data"][0]
            return {"value": int(entry["value"]), "label": entry["value_classification"]}
        except Exception:
            pass
        return {"value": 50, "label": "Neutral"}

    def fetch_funding_rate(self, symbol: str) -> float:
        try:
            result = subprocess.run(
                [_BGC_BIN, "futures", "futures_get_funding_rate", "--symbol", symbol],
                capture_output=True, text=True, timeout=10, env=os.environ.copy(),
            )
            data = json.loads(result.stdout)
            if data.get("ok", True) is not False:
                records = data.get("data", [])
                if records:
                    return float(records[0].get("fundingRate", 0.0))
        except Exception:
            pass
        return 0.0

    def fetch_volatility(self, symbol: str) -> float:
        try:
            result = subprocess.run(
                [_BGC_BIN, "spot", "spot_get_ticker", "--symbol", symbol],
                capture_output=True, text=True, timeout=10, env=os.environ.copy(),
            )
            data = json.loads(result.stdout)
            if data.get("ok", True) is not False:
                records = data.get("data", [])
                if records:
                    ticker = records[0]
                    high  = float(ticker["high24h"])
                    low   = float(ticker["low24h"])
                    open_ = float(ticker["open"])
                    if open_ > 0:
                        return (high - low) / open_ * 100
        except Exception:
            pass
        return 0.0

    # ── Risk score ───────────────────────────────────────────────────────────

    @staticmethod
    def _compute_base_risk_score(
        leverage: float,
        size_usdt: float,
        account_balance_usdt: float,
        daily_pnl_usdt: float,
        consecutive_losses: int,
    ) -> int:
        score = 0
        if leverage > 5:
            score += 20
        if leverage > 10:
            score += 20
        if account_balance_usdt > 0 and (size_usdt / account_balance_usdt) > 0.03:
            score += 20
        if daily_pnl_usdt < 0:
            score += 20
        if consecutive_losses >= 2:
            score += 20
        return score

    # ── 5 Layers ─────────────────────────────────────────────────────────────

    def _layer1_symbol_check(self, symbol: str) -> str | None:
        if symbol in self._rules["blocked_symbols"]:
            return f"{symbol} is on the blocked symbols list"
        if symbol not in self._rules["allowed_symbols"]:
            return (
                f"{symbol} is not in the allowed symbols list "
                f"({self._rules['allowed_symbols']})"
            )
        return None

    def _layer2_leverage_cap(self, leverage: float) -> str | None:
        cap = self._rules["leverage_cap"]
        if leverage > cap:
            return f"Leverage {leverage}x exceeds cap of {cap}x"
        return None

    def _layer3_position_size(self, size_usdt: float, account_balance_usdt: float) -> str | None:
        pct = self._rules["max_position_size_pct"]
        max_size = account_balance_usdt * pct / 100
        if size_usdt > max_size:
            return (
                f"Position size ${size_usdt:.2f} exceeds "
                f"{pct}% of account (${max_size:.2f})"
            )
        return None

    def _layer4_daily_loss_circuit(
        self, daily_pnl_usdt: float, account_balance_usdt: float
    ) -> str | None:
        pct = self._rules["daily_loss_limit_pct"]
        limit = -(account_balance_usdt * pct / 100)
        if daily_pnl_usdt < limit:
            return (
                f"Daily PnL ${daily_pnl_usdt:.2f} breaches "
                f"{pct}% daily loss limit (${limit:.2f})"
            )
        return None

    def _layer5_consecutive_loss_throttle(self, consecutive_losses: int) -> str | None:
        limit = self._rules["consecutive_loss_limit"]
        if consecutive_losses >= limit:
            return (
                f"{consecutive_losses} consecutive losses (≥ limit of {limit}): "
                "recommended size reduced 50%"
            )
        return None

    # ── Public check ─────────────────────────────────────────────────────────

    def check(
        self,
        symbol: str,
        side: str,
        size_usdt: float,
        leverage: float,
        account_balance_usdt: float,
        daily_pnl_usdt: float,
        consecutive_losses: int,
    ) -> RiskCheckResult:
        base_score = self._compute_base_risk_score(
            leverage, size_usdt, account_balance_usdt,
            daily_pnl_usdt, consecutive_losses,
        )

        # ── Hard layers 1-4 ──────────────────────────────────────────────────
        hard_layers = [
            ("symbol_check",       lambda: self._layer1_symbol_check(symbol)),
            ("leverage_cap",       lambda: self._layer2_leverage_cap(leverage)),
            ("position_size",      lambda: self._layer3_position_size(size_usdt, account_balance_usdt)),
            ("daily_loss_circuit", lambda: self._layer4_daily_loss_circuit(daily_pnl_usdt, account_balance_usdt)),
        ]

        for layer_name, check_fn in hard_layers:
            block_reason = check_fn()
            passed = block_reason is None

            if self._audit:
                self._audit.log_risk_check(
                    symbol=symbol,
                    layer_name=layer_name,
                    passed=passed,
                    reason=block_reason or "passed",
                    value_checked=self._layer_value(
                        layer_name, leverage, size_usdt, account_balance_usdt, daily_pnl_usdt,
                    ),
                    threshold=self._layer_threshold(layer_name, account_balance_usdt),
                )

            if not passed:
                return RiskCheckResult(
                    approved=False,
                    agent_instruction="BLOCKED",
                    blocking_layer=layer_name,
                    reason=block_reason,
                    recommended_size_usdt=size_usdt,
                    recommended_leverage=int(leverage),
                    original_size_usdt=size_usdt,
                    original_leverage=int(leverage),
                    fear_greed_index=50,
                    fear_greed_label="Neutral",
                    funding_rate=0.0,
                    volatility_24h=0.0,
                    risk_score=min(base_score, 100),
                )

        # ── Layer 5 — non-blocking throttle ──────────────────────────────────
        warnings: list[str] = []
        throttle_warn = self._layer5_consecutive_loss_throttle(consecutive_losses)
        if throttle_warn:
            warnings.append(throttle_warn)
            if self._audit:
                self._audit.log_risk_check(
                    symbol=symbol,
                    layer_name="consecutive_loss_throttle",
                    passed=True,
                    reason=throttle_warn,
                    value_checked=float(consecutive_losses),
                    threshold=float(self._rules["consecutive_loss_limit"]),
                )

        # ── Fetch live market intelligence ────────────────────────────────────
        fear_greed   = self.fetch_fear_greed()
        funding_rate = self.fetch_funding_rate(symbol)
        volatility   = self.fetch_volatility(symbol)

        mc = self._rules.get("market_conditions", {})
        fg_extreme_greed  = mc.get("fear_greed_extreme_greed", 75)
        fg_extreme_fear   = mc.get("fear_greed_extreme_fear", 25)
        fr_high           = mc.get("funding_rate_high", 0.10)
        fr_negative       = mc.get("funding_rate_negative", -0.05)
        vol_high          = mc.get("volatility_high_pct", 4.0)
        vol_lev_cap       = mc.get("volatility_leverage_cap", 5)
        size_factor       = self._rules.get("size_reduction_factor", 0.5)

        recommended_size     = size_usdt
        recommended_leverage = int(leverage)
        size_adj_reason      = ""
        lev_adj_reason       = ""
        size_reduced         = False
        lev_reduced          = False

        fg_val = fear_greed["value"]

        # Fear & Greed adjustments
        if fg_val > fg_extreme_greed and side.lower() == "buy":
            recommended_size = size_usdt * size_factor
            size_adj_reason = (
                f"Extreme Greed ({fg_val}) detected - long position size reduced 50%"
            )
            warnings.append(size_adj_reason)
            size_reduced = True

        if fg_val < fg_extreme_fear and side.lower() == "sell":
            recommended_size = size_usdt * size_factor
            size_adj_reason = (
                f"Extreme Fear ({fg_val}) detected - short position size reduced 50%"
            )
            warnings.append(size_adj_reason)
            size_reduced = True

        # Funding rate warnings
        if funding_rate > fr_high and side.lower() == "buy":
            msg = (
                f"High positive funding rate ({funding_rate:.4f}) - "
                "longs paying heavily, consider timing"
            )
            warnings.append(msg)

        if funding_rate < fr_negative and side.lower() == "sell":
            msg = (
                f"Negative funding rate ({funding_rate:.4f}) - "
                "shorts paying, consider timing"
            )
            warnings.append(msg)

        # Volatility leverage reduction
        if volatility > vol_high and int(leverage) > vol_lev_cap:
            recommended_leverage = vol_lev_cap
            lev_adj_reason = (
                f"High volatility ({volatility:.1f}%) - leverage reduced to {vol_lev_cap}x"
            )
            warnings.append(lev_adj_reason)
            lev_reduced = True

        # ── Agent instruction ─────────────────────────────────────────────────
        if size_reduced and lev_reduced:
            instruction = "REDUCE_SIZE_AND_LEVERAGE"
        elif size_reduced:
            instruction = "REDUCE_SIZE"
        elif lev_reduced:
            instruction = "REDUCE_SIZE_AND_LEVERAGE"
        elif fg_val > 90 or fg_val < 10:
            instruction = "WAIT"
        else:
            instruction = "PROCEED"

        # ── Final risk score ──────────────────────────────────────────────────
        risk_score = base_score
        if fg_val > 75:
            risk_score += 15
        if fg_val < 25:
            risk_score += 15
        if abs(funding_rate) > 0.1:
            risk_score += 10
        if volatility > 4.0:
            risk_score += 10
        risk_score = min(risk_score, 100)

        # ── Audit log: market intelligence layer ──────────────────────────────
        if self._audit:
            self._audit.log_risk_check(
                symbol=symbol,
                layer_name="market_intelligence",
                passed=True,
                reason=f"instruction={instruction}",
                fear_greed_index=fg_val,
                funding_rate=funding_rate,
                volatility_24h=volatility,
                agent_instruction=instruction,
                recommended_size_usdt=recommended_size,
                recommended_leverage=recommended_leverage,
            )

        return RiskCheckResult(
            approved=True,
            agent_instruction=instruction,
            blocking_layer=None,
            reason="All risk layers passed",
            recommended_size_usdt=round(recommended_size, 2),
            recommended_leverage=recommended_leverage,
            original_size_usdt=size_usdt,
            original_leverage=int(leverage),
            fear_greed_index=fg_val,
            fear_greed_label=fear_greed["label"],
            funding_rate=funding_rate,
            volatility_24h=round(volatility, 4),
            risk_score=risk_score,
            warnings=warnings,
            size_adjustment_reason=size_adj_reason,
            leverage_adjustment_reason=lev_adj_reason,
        )

    # ── Symbol management ─────────────────────────────────────────────────────

    def add_allowed_symbol(self, symbol: str) -> bool:
        if symbol in self._rules["allowed_symbols"]:
            return False
        self._rules["allowed_symbols"].append(symbol)
        self._save_rules()
        return True

    def remove_allowed_symbol(self, symbol: str) -> bool:
        if symbol not in self._rules["allowed_symbols"]:
            return False
        self._rules["allowed_symbols"].remove(symbol)
        self._save_rules()
        return True

    def add_blocked_symbol(self, symbol: str) -> bool:
        if symbol in self._rules["blocked_symbols"]:
            return False
        self._rules["blocked_symbols"].append(symbol)
        self._save_rules()
        return True

    def remove_blocked_symbol(self, symbol: str) -> bool:
        if symbol not in self._rules["blocked_symbols"]:
            return False
        self._rules["blocked_symbols"].remove(symbol)
        self._save_rules()
        return True

    def get_symbol_lists(self) -> dict:
        return {
            "allowed": list(self._rules["allowed_symbols"]),
            "blocked": list(self._rules["blocked_symbols"]),
        }

    def get_summary(self) -> dict:
        return {"config_path": self._config_path, "rules": dict(self._rules)}

    # ── Helpers for audit logging ─────────────────────────────────────────────

    def _layer_value(
        self,
        layer_name: str,
        leverage: float,
        size_usdt: float,
        account_balance_usdt: float,
        daily_pnl_usdt: float,
    ) -> float | None:
        return {
            "leverage_cap":       leverage,
            "position_size":      size_usdt,
            "daily_loss_circuit": daily_pnl_usdt,
        }.get(layer_name)

    def _layer_threshold(self, layer_name: str, account_balance_usdt: float) -> float | None:
        r = self._rules
        return {
            "leverage_cap":       float(r["leverage_cap"]),
            "position_size":      account_balance_usdt * r["max_position_size_pct"] / 100,
            "daily_loss_circuit": -(account_balance_usdt * r["daily_loss_limit_pct"] / 100),
        }.get(layer_name)
