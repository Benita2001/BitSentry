# @0xbeni/bitsentry-mcp

BitSentry MCP server for Bitget trading agents.

## Prerequisites

```bash
pip install bitsentry
```

## Usage

Add to Claude Code MCP config (~/.claude/settings.json):

```json
{
  "mcpServers": {
    "bitsentry": {
      "command": "npx",
      "args": ["-y", "@0xbeni/bitsentry-mcp"],
      "env": {
        "BITGET_API_KEY": "your_api_key",
        "BITGET_SECRET_KEY": "your_secret_key",
        "BITGET_PASSPHRASE": "your_passphrase",
        "BITGET_DEMO_MODE": "true"
      }
    }
  }
}
```

## MCP Tools

- check_risk — 5-layer pre-trade safety check with Fear&Greed, funding rate, volatility
- get_position_safety — GREEN/YELLOW/RED per open position
- get_account_summary — Account safety rollup
- evaluate_strategy — PERFORMING/DEGRADING/DEAD verdict
- record_trade — Log completed trade
- get_audit_report — SHA-256 verified audit trail
- manage_symbols — Add/remove allowed trading pairs

## Python Package

```bash
pip install bitsentry
```

https://pypi.org/project/bitsentry/
