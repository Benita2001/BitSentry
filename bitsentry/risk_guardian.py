from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from bitsentry.audit_engine import AuditEngine


@dataclass
class RiskCheckResult:
    approved: bool
    blocking_layer: str | None
    reason: str
    warnings: list[str] = field(default_factory=list)
    risk_score: int = 0


class RiskGuardian:
    """
    5-layer pre-trade risk middleware.

    Layers run in order and stop at the first blocking failure.
    A non-blocking warning is added for consecutive loss throttle.
    """

    def __init__(
        self,
        config_path: str = "config/risk_rules.yaml",
        audit_engine: "AuditEngine | None" = None,
    ):
        self._rules = self._load_rules(config_path)
        self._audit = audit_engine
        self._config_path = config_path
        print(
            f"[bitsentry] RiskGuardian loaded rules from {config_path}: "
            f"leverage_cap={self._rules['leverage_cap']}, "
            f"max_position_size_pct={self._rules['max_position_size_pct']}%, "
            f"daily_loss_limit_pct={self._rules['daily_loss_limit_pct']}%, "
            f"consecutive_loss_limit={self._rules['consecutive_loss_limit']}, "
            f"allowed_symbols={self._rules['allowed_symbols']}"
        )

    # ── Config loading ───────────────────────────────────────────────────────

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
        return rules

    # ── Risk score ───────────────────────────────────────────────────────────

    @staticmethod
    def _compute_risk_score(
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
        return min(score, 100)

    # ── 5 Layers ─────────────────────────────────────────────────────────────

    def _layer1_symbol_check(self, symbol: str) -> str | None:
        """Returns a block reason string, or None if the symbol passes."""
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

    def _layer3_position_size(
        self, size_usdt: float, account_balance_usdt: float
    ) -> str | None:
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

    def _layer5_consecutive_loss_throttle(
        self, consecutive_losses: int
    ) -> str | None:
        """Returns a warning string if throttle applies, else None."""
        limit = self._rules["consecutive_loss_limit"]
        if consecutive_losses >= limit:
            return (
                f"{consecutive_losses} consecutive losses (≥ limit of {limit}): "
                "recommended size reduced 50%"
            )
        return None

    # ── Public API ───────────────────────────────────────────────────────────

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
        """
        Run all 5 layers in order.  Returns a RiskCheckResult.
        Layers 1-4 are hard blocks; layer 5 is a warning only.
        """
        risk_score = self._compute_risk_score(
            leverage, size_usdt, account_balance_usdt,
            daily_pnl_usdt, consecutive_losses,
        )
        warnings: list[str] = []

        layers = [
            ("symbol_check",          lambda: self._layer1_symbol_check(symbol)),
            ("leverage_cap",          lambda: self._layer2_leverage_cap(leverage)),
            ("position_size",         lambda: self._layer3_position_size(size_usdt, account_balance_usdt)),
            ("daily_loss_circuit",    lambda: self._layer4_daily_loss_circuit(daily_pnl_usdt, account_balance_usdt)),
        ]

        for layer_name, check_fn in layers:
            block_reason = check_fn()
            passed = block_reason is None

            if self._audit:
                self._audit.log_risk_check(
                    symbol=symbol,
                    layer_name=layer_name,
                    passed=passed,
                    reason=block_reason or "passed",
                    value_checked=self._layer_value(
                        layer_name, leverage, size_usdt,
                        account_balance_usdt, daily_pnl_usdt,
                    ),
                    threshold=self._layer_threshold(layer_name, account_balance_usdt),
                )

            if not passed:
                return RiskCheckResult(
                    approved=False,
                    blocking_layer=layer_name,
                    reason=block_reason,
                    warnings=warnings,
                    risk_score=risk_score,
                )

        # Layer 5 — non-blocking throttle warning
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

        return RiskCheckResult(
            approved=True,
            blocking_layer=None,
            reason="All risk layers passed",
            warnings=warnings,
            risk_score=risk_score,
        )

    def get_summary(self) -> dict:
        return {
            "config_path": self._config_path,
            "rules": dict(self._rules),
        }

    # ── Helpers for audit logging ────────────────────────────────────────────

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

    def _layer_threshold(
        self, layer_name: str, account_balance_usdt: float
    ) -> float | None:
        r = self._rules
        return {
            "leverage_cap":    float(r["leverage_cap"]),
            "position_size":   account_balance_usdt * r["max_position_size_pct"] / 100,
            "daily_loss_circuit": -(account_balance_usdt * r["daily_loss_limit_pct"] / 100),
        }.get(layer_name)
