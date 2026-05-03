"""Database models — SQLAlchemy ORM"""
from __future__ import annotations
import os, time
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, Float, String, Boolean, DateTime, Text
from sqlalchemy.orm import declarative_base, sessionmaker

_default_db = "sqlite:////tmp/quantbot.db" if os.getenv("VERCEL") else "sqlite:///data/quantbot.db"
DB_URL = os.getenv("DATABASE_URL", _default_db)
engine = create_engine(DB_URL, connect_args={"check_same_thread": False} if "sqlite" in DB_URL else {})
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id              = Column(Integer, primary_key=True, index=True)
    email           = Column(String, unique=True, index=True, nullable=False)
    username        = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    created_at      = Column(DateTime, default=datetime.utcnow)

    # Subscription
    subscription_tier    = Column(String, default="free")      # free | starter | pro | elite
    subscription_status  = Column(String, default="trialing")  # trialing | active | cancelled | past_due
    trial_ends_at        = Column(DateTime, nullable=True)
    stripe_customer_id   = Column(String, nullable=True)
    stripe_sub_id        = Column(String, nullable=True)
    revenue_share_pct    = Column(Float, default=1.00)         # User keeps 100% — flat-sub model (May 2026 pricing change)

    # Wallet / bank
    wallet_address   = Column(String, nullable=True)           # ETH/EVM wallet
    wallet_type      = Column(String, nullable=True)           # metamask | coinbase | walletconnect
    plaid_access_token = Column(String, nullable=True)
    plaid_item_id    = Column(String, nullable=True)
    bank_name        = Column(String, nullable=True)
    bank_mask        = Column(String, nullable=True)           # last 4 digits

    # Trading mode
    trading_mode        = Column(String, default="passive")   # passive | aggressive
    bot_enabled         = Column(Boolean, default=False)
    bot_started_at      = Column(DateTime, nullable=True)

    # Onboarding
    onboarding_complete = Column(Boolean, default=False)
    onboarding_step     = Column(Integer, default=0)

    # Trading balance
    virtual_balance  = Column(Float, default=0.0)
    total_deposited  = Column(Float, default=0.0)

    # Revenue tracking
    gross_profit     = Column(Float, default=0.0)   # total profit generated
    platform_cut     = Column(Float, default=0.0)   # platform kept
    net_profit       = Column(Float, default=0.0)   # user kept


class Portfolio(Base):
    __tablename__ = "portfolios"
    id         = Column(Integer, primary_key=True, index=True)
    user_id    = Column(Integer, index=True)
    timestamp  = Column(DateTime, default=datetime.utcnow)
    value      = Column(Float, default=0.0)
    cash       = Column(Float, default=0.0)
    positions  = Column(Text, default="{}")


class Trade(Base):
    __tablename__ = "trades"
    id          = Column(Integer, primary_key=True, index=True)
    user_id     = Column(Integer, index=True)
    symbol      = Column(String)
    side        = Column(String)
    qty         = Column(Float)
    price       = Column(Float)
    pnl         = Column(Float, default=0.0)
    strategy    = Column(String, default="")
    timestamp   = Column(DateTime, default=datetime.utcnow)


class Deposit(Base):
    __tablename__ = "deposits"
    id         = Column(Integer, primary_key=True, index=True)
    user_id    = Column(Integer, index=True)
    amount     = Column(Float)
    method     = Column(String, default="card")  # card | bank | crypto
    status     = Column(String, default="completed")
    stripe_pi  = Column(String, nullable=True)
    tx_hash    = Column(String, nullable=True)
    timestamp  = Column(DateTime, default=datetime.utcnow)


class Subscription(Base):
    __tablename__ = "subscriptions"
    id             = Column(Integer, primary_key=True, index=True)
    user_id        = Column(Integer, index=True, unique=True)
    tier           = Column(String, default="free")
    status         = Column(String, default="trialing")
    stripe_sub_id  = Column(String, nullable=True)
    current_period_end = Column(DateTime, nullable=True)
    created_at     = Column(DateTime, default=datetime.utcnow)
    updated_at     = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ══════════════════════════════════════════════════════════════════════════════
#  Subscription tier definitions
#
#  Pricing strategy (post-research, May 2026):
#    - Subscription-only — NO revenue share. Revenue share + sub double-dips
#      and kills the funnel for small accounts ($1K @ 1.5%/mo = $15 profit;
#      a $29 sub + 20% cut would leave the user net negative).
#    - Tiers scale with AI compute (LLM calls/day) + deployed capital cap +
#      access to advanced features (options, custom strategies).
#    - Aligned with WunderTrading $20/$45/$90, Bitsgap $18/$44/$110,
#      3Commas $15-$110. Coinrule Fund tier ($749/mo) is what we beat with
#      our Elite at $199/mo.
#    - Annual billing: 2 months free (16.7% off) — standard SaaS practice.
#    - Performance fee is OPT-IN only on Elite, not bundled — keeps trust.
# ══════════════════════════════════════════════════════════════════════════════
TIERS = {
    "free": {
        "name": "Free",
        "tagline": "Test the brain risk-free",
        "price_monthly": 0,
        "price_annual":  0,
        "deployed_cap":  0,
        "llm_calls_day": 50,
        "revenue_share": 1.00,        # user keeps 100% — paper trading anyway
        "platform_cut":  0.0,
        "perf_fee":      0.0,
        "features": [
            "Paper trading only",
            "50 AI decisions/day (Claude haiku)",
            "All 13 core strategies in dry-run",
            "Mistake-detection journal",
            "Real-time scanner & charts",
            "30-day delayed live signals",
        ],
        "cta": "Start free",
        "stripe_price_id":        None,
        "stripe_price_id_annual": None,
        "color": "#6b7280",
        "popular": False,
    },
    "starter": {
        "name": "Starter",
        "tagline": "First-time live traders",
        "price_monthly": 19,
        "price_annual":  190,         # 16.7% off ($228 → $190)
        "deployed_cap":  5000,
        "llm_calls_day": 200,
        "revenue_share": 1.00,        # NO revenue share
        "platform_cut":  0.0,
        "perf_fee":      0.0,
        "features": [
            "Live trading: stocks + crypto",
            "Up to $5K deployed capital",
            "200 AI decisions/day",
            "All strategies + self-learning ON",
            "Real-time signals & WebSocket feed",
            "Email alerts on every trade",
            "Trade journal export (CSV)",
            "14-day free trial",
        ],
        "cta": "Start trial",
        "stripe_price_id":        os.getenv("STRIPE_PRICE_STARTER", "price_starter"),
        "stripe_price_id_annual": os.getenv("STRIPE_PRICE_STARTER_ANNUAL", "price_starter_annual"),
        "color": "#3b82f6",
        "popular": False,
    },
    "pro": {
        "name": "Pro",
        "tagline": "Serious traders ready to scale",
        "price_monthly": 59,
        "price_annual":  590,         # 16.7% off ($708 → $590)
        "deployed_cap":  50000,
        "llm_calls_day": 1000,
        "revenue_share": 1.00,        # NO revenue share
        "platform_cut":  0.0,
        "perf_fee":      0.0,
        "features": [
            "Up to $50K deployed capital",
            "1,000 AI decisions/day",
            "Priority Claude Sonnet (better reasoning)",
            "Options & insider-buy strategies",
            "Grok X/Twitter sentiment research",
            "Custom strategy weights",
            "Full backtesting (5y history)",
            "API access for custom alerts",
            "Priority email support",
        ],
        "cta": "Get Pro",
        "stripe_price_id":        os.getenv("STRIPE_PRICE_PRO", "price_pro"),
        "stripe_price_id_annual": os.getenv("STRIPE_PRICE_PRO_ANNUAL", "price_pro_annual"),
        "color": "#8b5cf6",
        "popular": True,              # featured tier
    },
    "elite": {
        "name": "Elite",
        "tagline": "Funds, family offices, power users",
        "price_monthly": 199,         # was 299 — Coinrule Fund tier was $749, we crush
        "price_annual":  1990,        # 16.7% off ($2388 → $1990)
        "deployed_cap":  None,        # unlimited
        "llm_calls_day": None,        # unlimited
        "revenue_share": 1.00,        # NO mandatory revenue share
        "platform_cut":  0.0,
        "perf_fee":      0.10,        # OPT-IN: 10% of profits above $10K/month
        "features": [
            "Unlimited deployed capital",
            "Unlimited AI decisions/day",
            "Claude Opus on every trade",
            "Multi-account / co-managed",
            "Custom strategies built for you",
            "White-glove onboarding (1:1 call)",
            "Dedicated account manager",
            "24/7 phone & Slack support",
            "Optional 10% perf fee >$10K/mo profits",
        ],
        "cta": "Talk to us",
        "stripe_price_id":        os.getenv("STRIPE_PRICE_ELITE", "price_elite"),
        "stripe_price_id_annual": os.getenv("STRIPE_PRICE_ELITE_ANNUAL", "price_elite_annual"),
        "color": "#f59e0b",
        "popular": False,
    },
}


Base.metadata.create_all(bind=engine)
