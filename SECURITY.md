# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| `main`  | Yes       |

## Reporting a vulnerability

**Do not** open a public GitHub issue for security bugs.

Email the maintainers at the contact on your Bibue35 GitHub profile, or use GitHub **Private vulnerability reporting** if enabled on the repository.

Include:

- Description and impact
- Steps to reproduce
- Affected paths (e.g. `web/auth.py`, `core/executor.py`)
- Suggested fix if you have one

We aim to acknowledge reports within **72 hours** and ship fixes on `main` when confirmed.

## Scope

In scope:

- Authentication, session, and JWT handling
- Stripe webhook verification
- Broker credential storage and `.env` handling
- Order execution and risk bypass paths
- LLM prompt injection affecting trade decisions

Out of scope:

- Social engineering of maintainers
- Denial of service without a reproducible code path
- Issues in third-party brokers (Alpaca, etc.) — report to them directly

## Safe defaults

- `TRADING_MODE=paper` in examples
- Live trading requires explicit configuration
- LLM layer ratifies signals; it does not replace risk engine checks
