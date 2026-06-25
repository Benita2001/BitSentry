# BitSentry

Safety, audit, and intelligence layer for Bitget trading agents and traders.

## Structure

- `bitsentry/bgc_client.py` — Bitget API client
- `bitsentry/audit_engine.py` — Trade audit and logging
- `bitsentry/risk_guardian.py` — Pre-trade risk enforcement
- `bitsentry/position_monitor.py` — Real-time position tracking
- `bitsentry/strategy_evaluator.py` — Strategy scoring and auditing
- `bitsentry/api/server.py` — FastAPI REST server (11 endpoints)
- `config/risk_rules.yaml` — Configurable risk parameters
- `run.py` — Server launcher with startup banner

## Setup

```bash
make install   # pip install -e .
make run       # start server on http://127.0.0.1:8000
make validate  # generate audit report + HTML
make verify    # verify SHA-256 integrity hash
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Server health + mode |
| GET | `/positions` | Open positions with safety ratings |
| GET | `/positions/summary` | GREEN/YELLOW/RED counts |
| GET | `/positions/safe-to-trade` | Pre-trade exposure check |
| POST | `/risk/check` | 5-layer risk middleware |
| GET | `/strategy/leaderboard` | Strategies ranked by profit factor |
| GET | `/strategy/{tag}` | Strategy health verdict |
| POST | `/strategy/record` | Record a trade result |
| GET | `/audit/report` | Full audit + SHA-256 hash |
| GET | `/audit/verify` | Verify integrity hash |
| GET | `/docs` | Swagger UI |

## Stack

Python 3.10+, FastAPI, SQLite, bgc CLI, Pydantic v2
