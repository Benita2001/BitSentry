# BitSentry — AGENTS.md

## What We Are Building
BitSentry is a pip-installable Python library (`pip install bitsentry`) that is the safety, audit, and intelligence layer for Bitget trading agents and traders. Built for the Bitget AI Base Camp Hackathon S1 — Track 2: Trading Infra.

## Core Mission
Every Bitget trading agent is a black box. When it loses money, nobody knows why. BitSentry fixes that by wrapping every trade decision with risk enforcement, audit logging, and performance intelligence.

## 5 Layers We Are Building
1. BGCClient — wrapper around bgc CLI for all Bitget data calls (DONE)
2. AuditEngine — SQLite logger + SHA-256 integrity verification (NEXT)
3. RiskGuardian — 5-layer risk middleware that approves or blocks trades
4. PositionMonitor — live position safety ratings (green/yellow/red)
5. StrategyEvaluator — rolling win rate tracker with PERFORMING/DEGRADING/DEAD verdict

## Plus
- FastAPI REST server exposing all layers as endpoints
- React dashboard for human traders
- MCP server for AI agent integration
- Makefile with: make install, make test, make validate, make verify

## Credentials (Demo Account)
BITGET_DEMO_API_KEY=bg_a3a670745f27aaee1609acfe408215aa
BITGET_DEMO_SECRET_KEY=912bf1e07cf01cc21ad485954d202b0e72fcab89877131129fa793d427fb50e9
BITGET_DEMO_PASSPHRASE=BitgetDemoKey

## Build Rules
- Always read existing files before editing them
- Never guess bgc tool names — run bgc --help first
- Test every file immediately after writing it
- One component at a time, confirm it works before moving on
- Demo mode: always use BGCClient(demo=True) in tests
- All bgc calls go through BGCClient, never call bgc directly in other files

## Current Status
- bitsentry/bgc_client.py — COMPLETE AND TESTED
- bitsentry/audit_engine.py — IN PROGRESS
- bitsentry/risk_guardian.py — TODO
- bitsentry/position_monitor.py — TODO
- bitsentry/strategy_evaluator.py — TODO

## Stack
Python 3.11+, FastAPI, SQLite, bgc CLI, React (dashboard), MCP protocol
