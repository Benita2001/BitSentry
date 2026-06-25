import os
import sys
from pathlib import Path

# Load .env if present
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    from dotenv import load_dotenv
    load_dotenv(env_path)

import uvicorn

VERSION = "0.1.0"
PORT = 8000
HOST = "127.0.0.1"

demo = os.environ.get("BITGET_DEMO", "true").lower() != "false"
mode_label = "DEMO / paper-trading" if demo else "LIVE"

def _banner_line(content: str, width: int = 60) -> str:
    return f"║  {content:<{width}}║"

def _build_banner() -> str:
    lines = [
        "╔══════════════════════════════════════════════════════════════╗",
        "║                        BitSentry 0.1.0                       ║",
        "║       Safety & Audit Layer for Bitget Trading Agents         ║",
        "╠══════════════════════════════════════════════════════════════╣",
        _banner_line(f"Mode     : {mode_label}"),
        _banner_line(f"Address  : http://{HOST}:{PORT}"),
        "╠══════════════════════════════════════════════════════════════╣",
        _banner_line("Endpoints:"),
        _banner_line("  GET  /                     root info"),
        _banner_line("  GET  /health               server health + mode"),
        _banner_line("  GET  /positions            open positions + ratings"),
        _banner_line("  GET  /positions/summary    GREEN/YELLOW/RED counts"),
        _banner_line("  GET  /positions/safe-to-trade?symbol=&direction="),
        _banner_line("  POST /risk/check           5-layer pre-trade risk check"),
        _banner_line("  GET  /strategy/leaderboard ranked by profit factor"),
        _banner_line("  GET  /strategy/{tag}       strategy health verdict"),
        _banner_line("  POST /strategy/record      record a trade result"),
        _banner_line("  GET  /audit/report         full audit + SHA-256 hash"),
        _banner_line("  GET  /audit/verify         verify integrity hash"),
        _banner_line("  GET  /docs                 Swagger UI"),
        "╠══════════════════════════════════════════════════════════════╣",
        _banner_line("Press Ctrl+C to stop"),
        "╚══════════════════════════════════════════════════════════════╝",
    ]
    return "\n".join(lines)

BANNER = _build_banner()

if __name__ == "__main__":
    print(BANNER)
    sys.stdout.flush()
    uvicorn.run(
        "bitsentry.api.server:app",
        host=HOST,
        port=PORT,
        log_level="info",
        reload=False,
    )
