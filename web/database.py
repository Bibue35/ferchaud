"""Database models — SQLAlchemy ORM"""
from __future__ import annotations
import os, time
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, Float, String, Boolean, DateTime, Text, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker

_default_db = "sqlite:////tmp/quantbot.db" if os.getenv("VERCEL") else "sqlite:///data/quantbot.db"
DB_URL = os.getenv("DATABASE_URL", _default_db)
engine = create_engine(DB_URL, connect_args={"check_same_thread": False} if "sqlite" in DB_URL else {})
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id              = Column(Integer, primary_key=True, index=True)
    supabase_id     = Column(String, unique=True, index=True, nullable=True)  # Supabase auth.users UUID
    email           = Column(String, unique=True, index=True, nullable=False)
    username        = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False, default="supabase_managed")
    auth_provider   = Column(String, default="email")          # email | google | apple | x
    created_at      = Column(DateTime, default=datetime.utcnow)

    # Subscription
    subscription_tier    = Column(String, default="free")      # free | starter | pro | elite
    subscription_status  = Column(String, default="trialing")  # trialing | active | cancelled | past_due
    trial_ends_at        = Column(DateTime, nullable=True)
    stripe_customer_id   = Column(String, nullable=True)
    stripe_sub_id        = Column(String, nullable=True)
    revenue_share_pct    = Column(Float, default=0.80)         # fraction user keeps (e.g. 0.80 = 80%)

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

    # Onboarding profile
    trading_goal       = Column(String, nullable=True)   # grow | income | learn | protect | active
    risk_tolerance     = Column(String, nullable=True)   # conservative | moderate | balanced | aggressive | maximum
    experience_level   = Column(String, nullable=True)   # beginner | some | intermediate | advanced | professional
    investment_horizon = Column(String, nullable=True)   # short | medium | long | very_long
    starting_capital   = Column(String, nullable=True)   # under_500 | 500_2500 | 2500_10k | 10k_50k | 50k_plus

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


# Subscription tier definitions
TIERS = {
    "free": {
        "name": "Free",
        "price_monthly": 0,
        "revenue_share": 0.70,   # user keeps 70%
        "platform_cut": 0.30,
        "features": ["Up to $500 deployed", "Basic strategies", "24h delay on signals"],
        "stripe_price_id": None,
        "color": "#6b7280",
    },
    "starter": {
        "name": "Starter",
        "price_monthly": 29,
        "revenue_share": 0.80,   # user keeps 80%
        "platform_cut": 0.20,
        "features": ["Up to $2K deployed", "All strategies", "Live signals", "Email alerts"],
        "stripe_price_id": os.getenv("STRIPE_PRICE_STARTER", "price_starter"),
        "color": "#3b82f6",
    },
    "pro": {
        "name": "Pro",
        "price_monthly": 99,
        "revenue_share": 0.90,   # user keeps 90%
        "platform_cut": 0.10,
        "features": ["Up to $10K deployed", "All strategies + options", "Priority signals", "API access", "Grok research"],
        "stripe_price_id": os.getenv("STRIPE_PRICE_PRO", "price_pro"),
        "color": "#8b5cf6",
    },
    "elite": {
        "name": "Elite",
        "price_monthly": 299,
        "revenue_share": 0.98,   # user keeps 98%
        "platform_cut": 0.02,
        "features": ["Unlimited deployment", "White-glove setup", "Custom strategies", "Dedicated support", "0% revenue share on losses"],
        "stripe_price_id": os.getenv("STRIPE_PRICE_ELITE", "price_elite"),
        "color": "#f59e0b",
    },
}



class BrokerConnection(Base):
    __tablename__ = "broker_connections"

    id             = Column(Integer, primary_key=True)
    user_id        = Column(Integer, ForeignKey("users.id"), nullable=False)
    broker_name    = Column(String, nullable=False)   # "alpaca", "tradier", etc.
    display_name   = Column(String)                   # user's custom label
    api_key        = Column(String)
    api_secret     = Column(String)
    account_id     = Column(String)                   # for brokers that need it
    access_token   = Column(String)                   # for OAuth brokers
    refresh_token  = Column(String)
    extra          = Column(Text)                     # JSON for extra fields (username, etc.)
    is_paper       = Column(Boolean, default=False)
    is_active      = Column(Boolean, default=True)
    created_at     = Column(DateTime, default=datetime.utcnow)
    last_connected = Column(DateTime)


Base.metadata.create_all(bind=engine)
