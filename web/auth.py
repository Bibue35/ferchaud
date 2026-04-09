"""Authentication — Supabase Auth with local profile sync."""
import os
import warnings
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer

from web.database import SessionLocal, User, Portfolio, Deposit
from web.supabase_client import supabase


# ─── SupabaseUser wrapper ─────────────────────────────────────────────────────

class SupabaseUser:
    """Lightweight user object that mirrors the old SQLAlchemy User interface.

    Attributes come from the local DB profile so existing template code
    like ``user.email``, ``user.subscription_tier``, etc. keeps working.
    """

    def __init__(self, *, id: int, supabase_id: str, email: str, username: str,
                 subscription_tier: str = "free", subscription_status: str = "trialing",
                 virtual_balance: float = 0.0, total_deposited: float = 0.0,
                 onboarding_complete: bool = False, onboarding_step: int = 0,
                 trading_mode: str = "passive", bot_enabled: bool = False,
                 wallet_address: Optional[str] = None, wallet_type: Optional[str] = None,
                 stripe_customer_id: Optional[str] = None, stripe_sub_id: Optional[str] = None,
                 revenue_share_pct: float = 0.80,
                 gross_profit: float = 0.0, platform_cut: float = 0.0,
                 net_profit: float = 0.0, bot_started_at=None,
                 trial_ends_at=None, auth_provider: str = "email",
                 created_at=None, **kwargs):
        self.id = id
        self.supabase_id = supabase_id
        self.email = email
        self.username = username
        self.subscription_tier = subscription_tier
        self.subscription_status = subscription_status
        self.virtual_balance = virtual_balance
        self.total_deposited = total_deposited
        self.onboarding_complete = onboarding_complete
        self.onboarding_step = onboarding_step
        self.trading_mode = trading_mode
        self.bot_enabled = bot_enabled
        self.wallet_address = wallet_address
        self.wallet_type = wallet_type
        self.stripe_customer_id = stripe_customer_id
        self.stripe_sub_id = stripe_sub_id
        self.revenue_share_pct = revenue_share_pct
        self.gross_profit = gross_profit
        self.platform_cut = platform_cut
        self.net_profit = net_profit
        self.bot_started_at = bot_started_at
        self.trial_ends_at = trial_ends_at
        self.auth_provider = auth_provider
        self.created_at = created_at


def _user_from_db_row(row: User) -> SupabaseUser:
    """Convert a SQLAlchemy User row into a SupabaseUser."""
    return SupabaseUser(
        id=row.id,
        supabase_id=row.supabase_id or "",
        email=row.email,
        username=row.username,
        subscription_tier=row.subscription_tier or "free",
        subscription_status=row.subscription_status or "trialing",
        virtual_balance=row.virtual_balance or 0.0,
        total_deposited=row.total_deposited or 0.0,
        onboarding_complete=row.onboarding_complete or False,
        onboarding_step=row.onboarding_step or 0,
        trading_mode=row.trading_mode or "passive",
        bot_enabled=row.bot_enabled or False,
        wallet_address=row.wallet_address,
        wallet_type=row.wallet_type,
        stripe_customer_id=row.stripe_customer_id,
        stripe_sub_id=row.stripe_sub_id,
        revenue_share_pct=row.revenue_share_pct or 0.80,
        gross_profit=row.gross_profit or 0.0,
        platform_cut=row.platform_cut or 0.0,
        net_profit=row.net_profit or 0.0,
        bot_started_at=row.bot_started_at,
        trial_ends_at=row.trial_ends_at,
        auth_provider=row.auth_provider or "email",
        created_at=row.created_at,
    )


def _get_or_create_profile(supabase_id: str, email: str, username: str,
                           auth_provider: str = "email") -> SupabaseUser:
    """Look up or create a local User profile linked to a Supabase auth user."""
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.supabase_id == supabase_id).first()
        if not user:
            # Check if there's an existing user with the same email (migration case)
            user = db.query(User).filter(User.email == email).first()
            if user:
                # Link existing profile to Supabase
                user.supabase_id = supabase_id
                user.auth_provider = auth_provider
                db.commit()
                db.refresh(user)
            else:
                # Ensure unique username
                base_username = username or email.split("@")[0]
                final_username = base_username[:20]
                counter = 1
                while db.query(User).filter(User.username == final_username).first():
                    suffix = str(counter)
                    final_username = base_username[:20 - len(suffix)] + suffix
                    counter += 1

                user = User(
                    email=email,
                    username=final_username,
                    hashed_password="supabase_managed",
                    auth_provider=auth_provider,
                    supabase_id=supabase_id,
                )
                db.add(user)
                db.flush()

                # Create default portfolio
                portfolio = Portfolio(user_id=user.id, value=0.0, cash=0.0)
                db.add(portfolio)
                db.commit()
                db.refresh(user)

        return _user_from_db_row(user)
    finally:
        db.close()


# ─── Deprecated helpers (backward compat) ─────────────────────────────────────

def hash_password(password: str) -> str:
    """Deprecated: Supabase handles password hashing. Kept for backward compat."""
    warnings.warn("hash_password is deprecated — Supabase manages passwords", DeprecationWarning, stacklevel=2)
    import bcrypt
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Deprecated: Supabase handles password verification. Kept for backward compat."""
    warnings.warn("verify_password is deprecated — Supabase manages passwords", DeprecationWarning, stacklevel=2)
    import bcrypt
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def create_token(data: dict) -> str:
    """Deprecated: Supabase issues JWTs. Kept for backward compat."""
    warnings.warn("create_token is deprecated — Supabase issues tokens", DeprecationWarning, stacklevel=2)
    return ""


# ─── Core auth functions ──────────────────────────────────────────────────────

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(request: Request, token: Optional[str] = None) -> Optional[SupabaseUser]:
    """Validate session from Supabase cookies OR legacy JWT cookie.

    Try order:
    1. sb-access-token cookie  → validate with Supabase
    2. legacy 'token' cookie   → validate with local JWT (backward compat)
    """
    # ── 1. Try Supabase token ─────────────────────────────────────────────────
    access_token = request.cookies.get("sb-access-token")
    if access_token:
        try:
            user_response = supabase.auth.get_user(access_token)
            sb_user = user_response.user
            if sb_user:
                supabase_id = sb_user.id
                email = sb_user.email or ""
                user_metadata = sb_user.user_metadata or {}
                username = user_metadata.get("username", email.split("@")[0])
                provider = (sb_user.app_metadata or {}).get("provider", "email")
                return _get_or_create_profile(supabase_id, email, username, auth_provider=provider)
        except Exception:
            pass  # Fall through to legacy JWT

    # ── 2. Legacy JWT fallback ────────────────────────────────────────────────
    jwt_token = token or request.cookies.get("token")
    if not jwt_token:
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            jwt_token = auth_header[7:]

    if jwt_token:
        try:
            from jose import jwt as jose_jwt, JWTError
            SECRET_KEY = os.environ.get("JWT_SECRET", "qb-secret-change-in-production-2026")
            payload = jose_jwt.decode(jwt_token, SECRET_KEY, algorithms=["HS256"])
            user_id = payload.get("sub")
            if user_id:
                db = SessionLocal()
                try:
                    from web.database import User as DbUser
                    db_user = db.query(DbUser).filter(DbUser.id == int(user_id)).first()
                    if db_user:
                        return _user_from_db_row(db_user)
                finally:
                    db.close()
        except Exception:
            pass

    return None


def require_user(request: Request, token: Optional[str] = None) -> SupabaseUser:
    """Require authenticated user — raises 401 if not logged in."""
    user = get_current_user(request, token)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def signup_user(email: str, username: str, password: str, full_name: str = "") -> dict:
    """Create a new user via Supabase Auth."""
    try:
        response = supabase.auth.sign_up({
            "email": email,
            "password": password,
            "options": {
                "data": {
                    "username": username,
                    "full_name": full_name,
                }
            }
        })

        if not response.user:
            return {"error": "Signup failed — please try again"}

        # Check if email confirmation is required
        if response.user.identities is not None and len(response.user.identities) == 0:
            return {"error": "Email already registered"}

        session = response.session
        if not session:
            # Email confirmation required — user created but not yet confirmed
            return {
                "message": "Check your email to confirm your account",
                "user_id": response.user.id,
                "username": username,
                "needs_confirmation": True,
            }

        # Session available — create local profile
        supabase_id = response.user.id
        profile = _get_or_create_profile(supabase_id, email, username)

        return {
            "access_token": session.access_token,
            "refresh_token": session.refresh_token,
            "user_id": profile.id,
            "username": profile.username,
        }
    except Exception as e:
        error_msg = str(e)
        if "already registered" in error_msg.lower() or "already been registered" in error_msg.lower():
            return {"error": "Email already registered"}
        return {"error": error_msg}


def login_user(email: str, password: str) -> dict:
    """Sign in via Supabase Auth."""
    try:
        response = supabase.auth.sign_in_with_password({
            "email": email,
            "password": password,
        })

        if not response.user or not response.session:
            return {"error": "Invalid email or password"}

        supabase_id = response.user.id
        user_metadata = response.user.user_metadata or {}
        username = user_metadata.get("username", email.split("@")[0])
        provider = (response.user.app_metadata or {}).get("provider", "email")
        profile = _get_or_create_profile(supabase_id, email, username, auth_provider=provider)

        return {
            "access_token": response.session.access_token,
            "refresh_token": response.session.refresh_token,
            "user_id": profile.id,
            "username": profile.username,
        }
    except Exception as e:
        error_msg = str(e)
        if "invalid" in error_msg.lower() or "credentials" in error_msg.lower():
            return {"error": "Invalid email or password"}
        return {"error": error_msg}


def deposit_funds(user_id: int, amount: float) -> dict:
    """Add funds to user's virtual balance."""
    if amount <= 0:
        return {"error": "Amount must be positive"}
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return {"error": "User not found"}
        user.virtual_balance = (user.virtual_balance or 0) + amount
        user.total_deposited = (user.total_deposited or 0) + amount
        deposit = Deposit(user_id=user.id, amount=amount)
        db.add(deposit)
        db.commit()
        return {"balance": user.virtual_balance, "deposited": user.total_deposited}
    except Exception as e:
        db.rollback()
        return {"error": str(e)}
    finally:
        db.close()
