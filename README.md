# BitSentry

**Safety, audit, and intelligence layer for Bitget trading agents and traders.**

> Built for the Bitget AI Base Camp Hackathon S1 — Track 2: Trading Infra

[![PyPI version](https://img.shields.io/pypi/v/bitsentry.svg)](https://pypi.org/project/bitsentry/)
[![Python](https://img.shields.io/pypi/pyversions/bitsentry.svg)](https://pypi.org/project/bitsentry/)
[![npm](https://img.shields.io/npm/v/@0xbeni/bitsentry-mcp.svg)](https://www.npmjs.com/package/@0xbeni/bitsentry-mcp)

## TL;DR for Judges

**What:** Safety, audit, and intelligence infrastructure for Bitget trading agents. Wraps every trade decision with 5-layer risk enforcement, tamper-proof audit logging, and live market intelligence.

**Install:** `pip install bitsentry` + `npx @0xbeni/bitsentry-mcp`

**Verify:**
```bash
make validate   # generates audit report with live Bitget market data
make verify     # recomputes SHA-256 hash — Integrity verified: True
```

**Live audit results (2026-06-28, Bitget API via bgc):**
| Metric | Value |
|--------|-------|
| BTC Price | $60,118.71 (Bitget live) |
| ETH Price | $1,580.71 (Bitget live) |
| Fear & Greed | 18 — Extreme Fear |
| BTC Volatility 24h | 1.97% |
| Risk checks run | 15 |
| Approved | 11 (73.3%) |
| Blocked | 4 (26.7%) |
| Integrity Hash | fc978a230fd19ac9103a5a64a257764215e0ce2049ee4a3bfffa68c3f6657c0d |
| Verified | ✅ True |

**Quick check (30 seconds):**
```bash
git clone https://github.com/Benita2001/BitSentry && cd BitSentry
pip install bitsentry
export BITGET_API_KEY="your_key"
export BITGET_SECRET_KEY="your_secret"
export BITGET_PASSPHRASE="your_passphrase"
make validate   # generates live audit report
make verify     # confirms SHA-256 integrity
```

---

## The Problem

Every AI trading agent a black box. When it loses money, nobody knows why. When it violates risk rules, nothing stops it. When its strategy degrades, there is no instrument to detect it before the losses mount.

BitSentry fixes this.

---

## What BitSentry Does

BitSentry sits between any Bitget trading agent and the exchange. Every trade decision passes through 5 layers before touching Bitget.

```
Trading Agent (any agent using Bitget Agent Hub)
        ↓
Layer 1: Risk Guardian     ← blocks or adjusts dangerous trades
        ↓
Layer 2: Audit Engine      ← logs every decision, SHA-256 verified
        ↓
Layer 3: Position Monitor  ← GREEN / YELLOW / RED per position
        ↓
Layer 4: Strategy Evaluator ← PERFORMING / DEGRADING / DEAD
        ↓
Layer 5: Report Generator  ← daily Telegram summary to trader
        ↓
Bitget Exchange (via bgc)
```

---

## 5 Layers Explained

### Layer 1 — Risk Guardian

5-layer pre-trade check that runs before every bgc order call.

| Sub-layer | Rule | Action |
|-----------|------|--------|
| Symbol Check | Symbol must be in allowed list |
| Leverage Cap | Max 10x (configurable) | BLOCKED if exceeded |
| Position Size | Max 5% of account per trade | BLOCKED if exceeded |
| Daily Loss Circuit | Stops trading if daily loss > 3% of account | BLOCKED |
| Consecutive Loss Throttle | 3+ losses in a row | WARNING + size reduced 50% |

**Market condition layer (runs after risk layers pass):**
- Fear & Greed > 75 + going long → size reduced 50%
- Fear & Greed < 25 + going short → size reduced 50%
- Funding rate > 0.1% + long → WARNING
- Volatility > 4% + leverage > 5x → leverage capped

**Returns `agent_instruction`:** `PROCEED` / `REDUCE_SIZE` / `REDUCE_SIZE_AND_LEVERAGE` / `WAIT` / `BLOCKED`

Agents don't read warning text. They read `agent_instruction` and act on it directly.

**Real output from 2026-06-28 run:**
```
[11:06:14] BTCUSDT  BUY  $100.00 x3  → APPROVED  PROCEED        risk=15/100
[11:06:21] BTCUSDT  SELL $100.00 x2  → APPROVED  REDUCE_SIZE    risk=15/100
           WARNING: Extreme Fear (18) → short size reduced to $50.00
[11:06:31] BTCUSDT  BUY  $625.00 x15 → BLOCKED   leverage_cap
           Reason: Leverage 15x exceeds cap of 10x
[11:06:31] XRPUSDT  BUY  $125.00 x5  → BLOCKED   symbol_check
           Reason: XRPUSDT not in allowed list
```

### Layer 2 — Audit Engine

Every decision — approved or blocked — logged to SQLite with full context:
- Timestamp
- Symbol, side, size, leverage
- Which risk layer made the decision
- Live Fear & Greed at time of decision
- Live volatility at time of decision
- Live funding rate at time of decision
- SHA-256 integrity hash over ALL records

Judges run `make verify` to recompute the hash independently. If it matches, nothing was tampered with.

**Real audit output:**
```
Total Risk Checks   : 64
Trade Intents Logged: 11
Approved            : 11 / 15 (73.3%)
Blocked             :  4 / 15 (26.7%)
Integrity Hash      : fc978a230fd19ac9103a5a64a257764215e0ce2049ee4a3bfffa68c3f6657c0d
Verified            : True ✅
```

### Layer 3 — Position Monitor

Real-time safety rating for every open Bitget position.

| Rating | Condition |
|--------|-----------|
| 🟢 GREEN | Unrealized PnL > -3% AND margin ratio < 0.5 |
| 🟡 YELLOW | PnL between -3% and -7% OR margin ratio > 0.5 |
| 🔴 RED | PnL < -7% OR margin ratio > 0.8 |

Also exposes `get_safe_to_trade(symbol, direction)` — agents call this before opening new positions to check current exposure.

### Layer 4 — Strategy Evaluator

Tracks win rate over rolling 7-day and 30-day windows.

| Verdict | Condition |
|---------|-----------|
| PERFORMING | Win rate 30d ≥ 55% AND profit factor ≥ 1.2 |
| DEGRADING | Win rate 30d < 55% OR 7d win rate dropped 10% below 30d |
| DEAD | Win rate 7d < 35% |

Agents query `evaluate_strategy(tag)` to check if their own strategy is still working — and self-adjust.

### Layer 5 — Report Generator

Automated trading summaries delivered to Telegram:
- Daily at 23:00 UTC
- Weekly every Sunday at 23:30 UTC
- Monthly on 1st at 23:45 UTC

Each report includes: trades approved vs blocked, position safety, strategy verdicts, audit integrity status.

---

## Architecture

```
bitsentry/
├── bitsentry/
│   ├── bgc_client.py         # Bitget Agent Hub bgc wrapper (153 lines)
│   ├── audit_engine.py       # SQLite + SHA-256 integrity (365 lines)
│   ├── risk_guardian.py      # 5-layer risk middleware + market intelligence (475 lines)
│   ├── position_monitor.py   # Live position safety ratings (223 lines)
│   ├── strategy_evaluator.py # Rolling performance tracker (269 lines)
│   ├── reporter.py           # Daily/weekly/monthly reports (227 lines)
│   ├── scheduler.py          # Automated report scheduler (81 lines)
│   ├── api/server.py         # FastAPI REST server — 21 endpoints (321 lines)
│   └── mcp/server.py         # MCP server — 7 tools (218 lines)
├── examples/
│   └── agent_hub_integration.py  # Full working integration example
├── config/risk_rules.yaml    # Configurable risk rules
├── validation/               # Live audit logs (committed)
│   ├── api_call_log.json     # 15 risk checks with live Bitget prices
│   ├── audit_report.json     # Full audit report with integrity hash
│   └── audit_report.html     # Human-readable audit report
├── npm-package/              # @0xbeni/bitsentry-mcp npm package
├── landing/                  # Vercel landing page
├── run.py                    # One-command server start
└── Makefile                  # install, run, test, validate, verify
```

**Total: 2,363 lines of Python across 13 files**

---

## Quick Start

BitSentry extends Bitget Agent Hub. Set up Agent Hub first.

### Step 1 — Install Bitget Agent Hub
```bash
npx bitget-hub upgrade-all --target claude
```

### Step 2 — Set credentials (same as Agent Hub, set once)
```bash
export BITGET_API_KEY="your_api_key"
export BITGET_SECRET_KEY="your_secret_key"
export BITGET_PASSPHRASE="your_passphrase"
export BITGET_DEMO_MODE="true"   # remove for live trading
```

### Step 3 — Install BitSentry
```bash
pip install bitsentry
```

### Step 4 — Add to Claude Code MCP config (~/.claude/settings.json)
```json
{
  "mcpServers": {
    "bitget": {
      "command": "npx",
      "args": ["-y", "bitget-mcp-server"]
    },
    "bitsentry": {
      "command": "npx",
      "args": ["-y", "@0xbeni/bitsentry-mcp"]
    }
  }
}
```

No API keys in config. BitSentry reads credentials from your environment automatically — same ones Bitget Agent Hub uses.

### Step 5 — Start the REST API server
```bash
make run
# Server starts at http://127.0.0.1:8000
# Swagger docs at http://127.0.0.1:8000/docs
```

---

## Integration — Python

```python
from bitsentry import RiskGuardian, AuditEngine

audit = AuditEngine()
guardian = RiskGuardian(audit_engine=audit)

# Run BEFORE every bgc order call
result = guardian.check(
    symbol="BTCUSDT",
    side="buy",
    size_usdt=100,
    leverage=5,
    account_balance_usdt=5000,
    daily_pnl_usdt=0,
    consecutive_losses=0
)

# Agent acts on structured instruction, not warning text
if result.agent_instruction == "PROCEED":
    # execute via bgc
    pass
elif result.agent_instruction == "REDUCE_SIZE":
    # use result.recommended_size_usdt instead
    pass
elif result.agent_instruction == "WAIT":
    # market conditions unfavorable, retry later
    pass
elif result.agent_instruction == "BLOCKED":
    # hard rule violation, do not trade
    print(f"Blocked by {result.blocking_layer}: {result.reason}")
```

See `examples/agent_hub_integration.py` for a complete working example with live Bitget data.

---

## MCP Tools (7 tools)

Your Claude Code agent gets these tools via `npx @0xbeni/bitsentry-mcp`:

| Tool | Description | Returns |
|------|-------------|---------|
| `check_risk` | 5-layer pre-trade check + live F&G, funding, volatility | agent_instruction, recommended_size, recommended_leverage, risk_score |
| `get_position_safety` | Safety rating per open position | GREEN / YELLOW / RED per position |
| `get_account_summary` | Account-level safety rollup | total_positions, green/yellow/red counts, overall_safety |
| `evaluate_strategy` | Strategy performance verdict | PERFORMING / DEGRADING / DEAD |
| `record_trade` | Log completed trade for tracking | WIN / LOSS, updated stats |
| `get_audit_report` | Full audit trail with SHA-256 hash | integrity_hash, verified |
| `manage_symbols` | Add/remove allowed trading pairs | updated symbol lists |

---

## REST API (21 endpoints)

Start: `make run` → `http://127.0.0.1:8000` | Swagger: `/docs`

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Server status |
| GET | `/positions` | Open positions with safety ratings |
| GET | `/positions/summary` | Account safety summary |
| GET | `/positions/safe-to-trade` | Pre-trade safety check |
| POST | `/risk/check` | Run 5-layer risk check |
| GET | `/symbols` | Current allowed/blocked symbol lists |
| POST | `/symbols/allow` | Add symbol to allowed list |
| DELETE | `/symbols/allow` | Remove from allowed list |
| POST | `/symbols/block` | Block a symbol |
| DELETE | `/symbols/block` | Unblock a symbol |
| GET | `/strategy/leaderboard` | All strategies ranked by profit factor |
| GET | `/strategy/{tag}` | Strategy health for specific tag |
| POST | `/strategy/record` | Record completed trade |
| GET | `/audit/report` | Full audit report + SHA-256 hash |
| GET | `/audit/verify` | Verify audit integrity |
| GET | `/report/daily` | Daily trading summary |
| GET | `/report/weekly` | Weekly summary |
| GET | `/report/monthly` | Monthly summary |
| POST | `/report/send` | Send report via Telegram immediately |

---

## Verify Audit Integrity

```bash
make validate
# Output:
# {'total_trade_intents': 11, 'total_risk_checks': 64, 'approval_rate': 73.3, ...
# 'integrity_hash': 'fc978a230fd19ac9103a5a64a257764215e0ce2049ee4a3bfffa68c3f6657c0d'}
# HTML report: validation/audit_report.html

make verify
# Output: Integrity verified: True
```

Judges can recompute the SHA-256 hash independently. If it matches the stored hash, no records were tampered with.

---

## Symbol Management

Add or remove trading pairs dynamically — no YAML editing needed:

```bash
# Via REST API
curl -X POST http://localhost:8000/symbols/allow -H "Content-Type: application/json" -d '{"symbol": "XRPUSDT"}'
curl -X DELETE http://localhost:8000/symbols/allow -H "Content-Type: application/json" -d '{"symbol": "XRPUSDT"}'

# Via MCP tool
# Agent calls: manage_symbols(action="allow", symbol="XRPUSDT")
```

---

## Automated Telegram Reports

```bash
export TELEGRAM_BOT_TOKEN=your_bot_token
export TELEGRAM_CHAT_ID=your_chat_id
```

Sample daily report:
```
📊 BitSentry Daily Report — 2026-06-28
────────────────────────────────────
📋 RISK ACTIVITY
  Total trade intents : 11
  Approved            : 8
  Blocked             : 3
  Top block reason    : leverage_cap
────────────────────────────────────
📍 POSITION SAFETY
  Overall safety : 🟢 GREEN
  Positions      : 🟢 0  🟡 0  🔴 0
  Unrealized PnL : +$0.00 USDT
────────────────────────────────────
📈 STRATEGY PERFORMANCE
  ✅ bitget-momentum  WR30d 72%  PF 6.00
────────────────────────────────────
🔒 AUDIT INTEGRITY
  SHA-256 : fc978a230fd19ac9...
  Status  : ✅ Verified
────────────────────────────────────
Generated by BitSentry v0.2.0
```

---

## Environment Variables

```bash
cp .env.example .env
```

| Variable | Required | Description |
|----------|----------|-------------|
| BITGET_API_KEY | Yes | Bitget API key (same as Agent Hub) |
| BITGET_SECRET_KEY | Yes | Bitget secret key |
| BITGET_PASSPHRASE | Yes | Bitget passphrase |
| BITGET_DEMO_MODE | No | "true" for paper trading |
| TELEGRAM_BOT_TOKEN | No | For automated daily reports |
| TELEGRAM_CHAT_ID | No | Your Telegram chat ID |

---

## What Judges Will See

```bash
# Install
pip install bitsentry

# Set credentials
export BITGET_API_KEY="your_key"
export BITGET_SECRET_KEY="your_secret"
export BITGET_PASSPHRASE="your_passphrase"
export BITGET_DEMO_MODE="true"

# Clone and run
git clone https://github.com/Benita2001/BitSentry && cd BitSentry

# Generate live audit report
make validate
# Fetches live BTC/ETH/SOL/DOGE prices from Bitget via bgc
# Fetches live Fear & Greed from alternative.me
# Runs 15 risk checks
# Generates validation/audit_report.html

# Verify integrity independently
make verify
# Recomputes SHA-256 hash from scratch
# Output: Integrity verified: True

# Run the server
make run
# Open http://127.0.0.1:8000/docs
# Call any endpoint directly from Swagger UI

# Run integration example
python3 examples/agent_hub_integration.py
# Shows BitSentry + Bitget Agent Hub working together live
```

---

## Known Limitations

- **Demo/paper trading only** in validation files — no real capital at risk. Risk rules and audit trail function identically in live mode.
- **Bitget API requires VPN** on some networks. bgc connects to Bitget's servers; ensure `bgc spot spot_get_ticker --symbol BTCUSDT` returns data before running.
- **Strategy Evaluator uses manual tags** — developers label trades with strategy name. Auto-detection planned for v0.3.0.
- **Position Monitor shows empty positions** on fresh demo account — real safety ratings activate once positions are open.

---

## Built With

- Python 3.10+ | FastAPI | SQLite | bgc CLI (Bitget Agent Hub)
- MCP protocol (Model Context Protocol)
- alternative.me Fear & Greed API
- Bitget REST API via bgc

## License

MIT

## Author

Built by [@0xbeni](https://x.com/0xbeni) for the Bitget AI Base Camp Hackathon S1 — Track 2: Trading Infra
