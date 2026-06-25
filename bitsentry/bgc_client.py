import json
import os
import subprocess
from typing import Any


class BGCError(Exception):
    """Raised when a bgc CLI call fails."""


class BGCClient:
    """
    Wraps the bgc CLI via subprocess.

    demo=True appends --paper-trading to every call and requires
    Demo API Key credentials in the environment.
    """

    def __init__(self, demo: bool = False):
        self.demo = demo
        self._verify_credentials()

    def _verify_credentials(self) -> None:
        missing = [
            v for v in ("BITGET_API_KEY", "BITGET_SECRET_KEY", "BITGET_PASSPHRASE")
            if not os.environ.get(v)
        ]
        if missing:
            raise BGCError(f"Missing environment variables: {', '.join(missing)}")

        # Quick connectivity check — public endpoint, no auth needed
        try:
            self.run("spot", "spot_get_ticker", symbol="BTCUSDT")
            mode = "DEMO/paper-trading" if self.demo else "LIVE"
            print(f"[bitsentry] BGCClient connected successfully ({mode})")
        except BGCError as exc:
            raise BGCError(f"BGCClient connection check failed: {exc}") from exc

    def run(self, module: str, tool: str, **params: Any) -> Any:
        """
        Execute: bgc [--paper-trading] <module> <tool> [--key value ...]

        Returns the parsed `data` field from the JSON response.
        Raises BGCError on non-zero exit code or error JSON.
        """
        cmd = ["bgc"]
        if self.demo:
            cmd.append("--paper-trading")
        cmd += [module, tool]
        for key, value in params.items():
            cmd += [f"--{key}", str(value)]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )

        raw = result.stdout.strip() or result.stderr.strip()

        if result.returncode != 0:
            # Try to parse structured error JSON from stderr
            try:
                payload = json.loads(result.stderr.strip())
                msg = payload.get("error", {}).get("message", result.stderr.strip())
                err_type = payload.get("error", {}).get("type", "Unknown")
                raise BGCError(f"[{err_type}] {msg}")
            except (json.JSONDecodeError, AttributeError):
                raise BGCError(result.stderr.strip() or f"bgc exited {result.returncode}")

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise BGCError(f"bgc returned non-JSON output: {raw[:200]}") from exc

        # Structured error inside a 200-exit response
        if isinstance(payload, dict) and payload.get("ok") is False:
            err = payload.get("error", {})
            raise BGCError(f"[{err.get('type', 'Error')}] {err.get('message', 'unknown error')}")

        return payload.get("data", payload)

    # ── High-level convenience methods ─────────────────────────────────────

    def get_ticker(self, symbol: str) -> dict:
        """Return spot ticker data for *symbol* (e.g. 'BTCUSDT')."""
        data = self.run("spot", "spot_get_ticker", symbol=symbol)
        # data is a list; return the first match
        if isinstance(data, list):
            for item in data:
                if item.get("symbol") == symbol:
                    return item
            return data[0] if data else {}
        return data

    def get_account_balance(self) -> list[dict]:
        """
        Return account asset balances.

        Note: the Bitget demo environment does not support the
        /api/v2/account/all-account-balance endpoint (returns 404).
        In that case a descriptive dict is returned instead of raising.
        """
        try:
            data = self.run("account", "get_account_assets")
            return data if isinstance(data, list) else [data]
        except BGCError as exc:
            if "404" in str(exc) or "NOT FOUND" in str(exc):
                return [
                    {
                        "warning": "account balance endpoint not available in demo environment",
                        "detail": str(exc),
                        "suggestion": "Use get_positions() for futures PnL or switch to a live key.",
                    }
                ]
            raise

    def get_positions(self, product_type: str = "USDT-FUTURES") -> list[dict]:
        """Return open futures positions for *product_type*."""
        data = self.run("futures", "futures_get_positions", productType=product_type)
        return data if isinstance(data, list) else []

    def get_order_history(self, symbol: str | None = None, product_type: str = "USDT-FUTURES") -> list[dict]:
        """
        Return recent open orders.

        Fetches spot unfilled orders (symbol required) and futures
        pending orders and combines them.
        """
        orders: list[dict] = []

        # Spot orders — symbol required
        if symbol:
            try:
                spot_data = self.run("spot", "spot_get_orders", symbol=symbol)
                if isinstance(spot_data, list):
                    orders.extend(spot_data)
            except BGCError:
                pass

        # Futures pending orders
        try:
            futures_data = self.run("futures", "futures_get_orders", productType=product_type)
            if isinstance(futures_data, dict):
                entries = futures_data.get("entrustedList") or []
                orders.extend(entries)
            elif isinstance(futures_data, list):
                orders.extend(futures_data)
        except BGCError:
            pass

        return orders
