"""
BitSentry + Bitget Agent Hub Integration Example

This shows how any trading agent can use:
- Bitget Agent Hub (58 trading APIs via bgc)
- BitSentry (safety, audit, and intelligence layer)

Together in one workflow.
"""

import os
from bitsentry import RiskGuardian, AuditEngine, PositionMonitor, StrategyEvaluator
from bitsentry.bgc_client import BGCClient

def run_safe_trade_example():
    """
    Example: Agent wants to buy BTCUSDT on Bitget.
    BitSentry checks safety BEFORE Agent Hub executes.
    """

    # Initialize BitSentry layers
    client = BGCClient(demo=True)
    audit = AuditEngine()
    guardian = RiskGuardian(audit_engine=audit)
    monitor = PositionMonitor(bgc_client=client, audit_engine=audit)

    print("=" * 60)
    print("BitSentry + Bitget Agent Hub Integration Demo")
    print("=" * 60)

    # Step 1: Get live market data from Bitget Agent Hub via bgc
    print("\n[1] Fetching live market data from Bitget Agent Hub...")
    ticker = client.get_ticker("BTCUSDT")
    btc_price = float(ticker.get("lastPr", 0))
    print(f"    BTC Price: ${btc_price:,.2f} USDT")

    # Step 2: Check current position safety
    print("\n[2] Checking current position safety...")
    summary = monitor.get_account_summary()
    print(f"    Overall Safety: {summary['overall_safety']}")
    print(f"    Open Positions: {summary['total_positions']}")

    # Step 3: Run BitSentry risk check BEFORE placing order
    print("\n[3] Running BitSentry 5-layer risk check...")
    trade_params = {
        "symbol": "BTCUSDT",
        "side": "buy",
        "size_usdt": 100,
        "leverage": 5,
        "account_balance_usdt": 5000,
        "daily_pnl_usdt": -20,
        "consecutive_losses": 1
    }

    result = guardian.check(**trade_params)

    if result.approved:
        print(f"    Status: APPROVED ✅")
        print(f"    Risk Score: {result.risk_score}/100")
        if result.warnings:
            print(f"    Warnings: {result.warnings}")

        # Step 4: Trade approved - would execute via Agent Hub bgc here
        print("\n[4] Trade approved by BitSentry.")
        print("    In production: bgc spot place_order --symbol BTCUSDT --side buy --size 100")
        print("    (Demo mode: order not placed)")

        # Log the intent
        audit.log_trade_intent(
            symbol="BTCUSDT",
            side="buy",
            size=0.01,
            leverage=5,
            signal_source="agent_hub_integration_example",
            reasoning=f"BTC at ${btc_price:,.2f}, risk check passed"
        )

    else:
        print(f"    Status: BLOCKED ❌")
        print(f"    Blocked by: {result.blocking_layer}")
        print(f"    Reason: {result.reason}")
        print("\n[4] Trade blocked by BitSentry. Order NOT sent to Bitget.")

    # Step 5: Show audit trail
    print("\n[5] Audit Trail Status:")
    report = audit.generate_audit_report()
    print(f"    Total decisions logged: {report['total_risk_checks']}")
    print(f"    Integrity hash: {report['integrity_hash'][:16]}...")
    print(f"    Verified: {audit.verify_integrity(report['integrity_hash'])}")

    print("\n" + "=" * 60)
    print("Integration complete. BitSentry protected this agent.")
    print("=" * 60)

if __name__ == "__main__":
    run_safe_trade_example()
