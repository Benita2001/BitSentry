from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bitsentry.audit_engine import AuditEngine
    from bitsentry.bgc_client import BGCClient


@dataclass
class PositionSnapshot:
    symbol: str
    side: str               # "long" or "short"
    size: float             # base-asset size
    entry_price: float
    mark_price: float
    unrealized_pnl: float
    unrealized_pnl_pct: float   # as % of margin_used
    margin_used: float
    leverage: int
    safety_rating: str      # GREEN / YELLOW / RED
    safety_message: str
    timestamp: str


def _safe_float(val, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _safe_int(val, default: int = 1) -> int:
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return default


class PositionMonitor:
    """
    Fetches live Bitget futures positions and assigns safety ratings.

    Safety tiers:
      GREEN  — unrealized_pnl_pct > -3 % AND margin_ratio ≤ 0.5
      YELLOW — pnl_pct in [-7, -3) OR margin_ratio in (0.5, 0.8]
      RED    — pnl_pct < -7 % OR margin_ratio > 0.8
    """

    def __init__(
        self,
        bgc_client: "BGCClient",
        audit_engine: "AuditEngine | None" = None,
        poll_interval_seconds: int = 30,
    ):
        self._client = bgc_client
        self._audit = audit_engine
        self.poll_interval_seconds = poll_interval_seconds

    # ── Safety rating ────────────────────────────────────────────────────────

    @staticmethod
    def _rate(pnl_pct: float, margin_ratio: float) -> tuple[str, str]:
        """Return (rating, message) for a position."""
        if pnl_pct < -7.0 or margin_ratio > 0.8:
            if pnl_pct < -7.0:
                msg = f"Unrealized loss {pnl_pct:.2f}% exceeds 7% danger threshold"
            else:
                msg = f"Margin ratio {margin_ratio:.2f} exceeds 0.80 — liquidation risk"
            return "RED", msg

        if pnl_pct < -3.0 or margin_ratio > 0.5:
            if pnl_pct < -3.0:
                msg = f"Unrealized loss {pnl_pct:.2f}% in caution zone (-3% to -7%)"
            else:
                msg = f"Margin ratio {margin_ratio:.2f} elevated (above 0.50)"
            return "YELLOW", msg

        return "GREEN", f"Position healthy: PnL {pnl_pct:.2f}%, margin ratio {margin_ratio:.2f}"

    # ── Position parsing ─────────────────────────────────────────────────────

    def _parse(self, raw: dict) -> PositionSnapshot:
        """
        Convert a raw Bitget v2 position dict into a PositionSnapshot.

        Bitget v2 field names for /api/v2/mix/position/all-position:
          holdSide, total, openPriceAvg, markPrice, unrealizedPL,
          marginSize, leverage, marginRatio
        """
        symbol = raw.get("symbol", "UNKNOWN")
        side = raw.get("holdSide", "unknown")

        size = _safe_float(raw.get("total", raw.get("size", 0)))
        entry_price = _safe_float(raw.get("openPriceAvg", raw.get("openPrice", 0)))
        mark_price = _safe_float(raw.get("markPrice", entry_price))
        unrealized_pnl = _safe_float(raw.get("unrealizedPL", raw.get("unrealisedPnl", 0)))
        margin_used = _safe_float(raw.get("marginSize", raw.get("margin", 0)))
        leverage = _safe_int(raw.get("leverage", 1))

        # PnL as % of margin_used — avoids division-by-zero
        pnl_pct = (unrealized_pnl / margin_used * 100) if margin_used > 0 else 0.0

        # Bitget may supply marginRatio directly; otherwise estimate from keepMarginRate
        margin_ratio = _safe_float(
            raw.get("marginRatio", raw.get("keepMarginRate", 0.0))
        )

        rating, message = self._rate(pnl_pct, margin_ratio)

        return PositionSnapshot(
            symbol=symbol,
            side=side,
            size=size,
            entry_price=entry_price,
            mark_price=mark_price,
            unrealized_pnl=unrealized_pnl,
            unrealized_pnl_pct=round(pnl_pct, 4),
            margin_used=margin_used,
            leverage=leverage,
            safety_rating=rating,
            safety_message=message,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    # ── Public API ───────────────────────────────────────────────────────────

    def get_positions(self, product_type: str = "USDT-FUTURES") -> list[PositionSnapshot]:
        """Fetch all open futures positions and return rated snapshots."""
        raw_list = self._client.get_positions(product_type=product_type)
        snapshots = [self._parse(r) for r in raw_list]

        if self._audit and snapshots:
            for snap in snapshots:
                self._audit.log_risk_check(
                    symbol=snap.symbol,
                    layer_name="position_monitor",
                    passed=snap.safety_rating != "RED",
                    reason=snap.safety_message,
                    value_checked=snap.unrealized_pnl_pct,
                    threshold=-7.0,
                )

        return snapshots

    def get_account_summary(self, product_type: str = "USDT-FUTURES") -> dict:
        """Return aggregate safety summary across all open positions."""
        positions = self.get_positions(product_type=product_type)

        counts = {"GREEN": 0, "YELLOW": 0, "RED": 0}
        total_pnl = 0.0
        for p in positions:
            counts[p.safety_rating] += 1
            total_pnl += p.unrealized_pnl

        # Worst-of-all: RED > YELLOW > GREEN
        if counts["RED"] > 0:
            overall = "RED"
        elif counts["YELLOW"] > 0:
            overall = "YELLOW"
        else:
            overall = "GREEN"

        return {
            "total_positions": len(positions),
            "green_count": counts["GREEN"],
            "yellow_count": counts["YELLOW"],
            "red_count": counts["RED"],
            "overall_safety": overall,
            "total_unrealized_pnl": round(total_pnl, 4),
        }

    def get_safe_to_trade(self, symbol: str, direction: str) -> dict:
        """
        Check whether opening a new position on *symbol* is safe.

        Returns:
          safe: bool
          reason: str
          current_exposure: float  (total margin in open positions for symbol)
        """
        positions = self.get_positions()

        symbol_positions = [p for p in positions if p.symbol == symbol]
        current_exposure = sum(p.margin_used for p in symbol_positions)

        # Block if there is already a RED-rated position on the same symbol
        red_positions = [p for p in symbol_positions if p.safety_rating == "RED"]
        if red_positions:
            return {
                "safe": False,
                "reason": (
                    f"{symbol} already has a RED-rated position — "
                    "resolve it before adding more exposure"
                ),
                "current_exposure": current_exposure,
            }

        # Block if opening in the same direction as an existing position with negative PnL
        same_side = [
            p for p in symbol_positions
            if p.side == direction and p.unrealized_pnl_pct < -3.0
        ]
        if same_side:
            return {
                "safe": False,
                "reason": (
                    f"Existing {direction} position on {symbol} is down "
                    f"{same_side[0].unrealized_pnl_pct:.2f}% — averaging down blocked"
                ),
                "current_exposure": current_exposure,
            }

        return {
            "safe": True,
            "reason": (
                f"No blocking positions on {symbol}. "
                f"Current {symbol} exposure: ${current_exposure:.2f}"
            ),
            "current_exposure": current_exposure,
        }
