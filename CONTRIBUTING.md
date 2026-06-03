# Contributing to Ferchaud

Thank you for helping improve Ferchaud. This project is Apache 2.0 — see the `Apache License` file.

## Getting started

1. Fork and clone `https://github.com/Bibue35/ferchaud`
2. `python3 -m venv .venv && source .venv/bin/activate`
3. `pip install -r requirements.txt`
4. `cp .env.example .env` and set `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` (paper mode recommended)
5. `mkdir -p logs data && ./start.sh --web` for UI-only work, or `./start.sh` for full stack

## Pull requests

- Keep changes focused; one concern per PR when possible
- Match existing style in `core/`, `strategies/`, and `web/`
- UI changes must follow `design.md` (CSS variables, no inline hex, tabular-nums on figures)
- Trading changes must default to **paper** mode and respect risk limits in `config.py`
- Do not commit secrets (`.env`, API keys, Stripe live keys)

## Areas we welcome help

- Broker adapters under `core/brokers/`
- Strategy modules under `strategies/` with tests on historical data
- LLM provider integrations in `core/llm_brain.py`
- Docs, accessibility, and dashboard empty/error states

## Security

Report vulnerabilities privately — see `SECURITY.md`. Do not open public issues for exploitable bugs.
