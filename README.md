# bitsentry

Safety, audit, and intelligence layer for Bitget trading agents and traders.

## Structure

- `bitsentry/bgc_client.py` — Bitget API client
- `bitsentry/audit_engine.py` — Trade audit and logging
- `bitsentry/risk_guardian.py` — Pre-trade risk enforcement
- `bitsentry/position_monitor.py` — Real-time position tracking
- `bitsentry/strategy_evaluator.py` — Strategy scoring and auditing
- `config/risk_rules.yaml` — Configurable risk parameters

## Setup

```bash
pip install -e .
```
