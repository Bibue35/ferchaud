"""Authentication — JWT tokens, password hashing, user management."""
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from jose import JWTError, jwt
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer

from web.database import SessionLocal, User, Portfolio, Deposit

SECRET_KEY = os.environ.get("JWT_SECRET", "qb-secret-change-in-production-2026")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 72

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def create_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(request: Request, token: Optional[str] = None):
    """Get current user from JWT token (cookie or header)."""
    # Try cookie first
    if not token:
        token = request.cookies.get("token")
    # Then Authorization header
    if not token:
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        if user_id is None:
            return None
        db = SessionLocal()
        user = db.query(User).filter(User.id == int(user_id)).first()
        db.close()
        return user
    except (JWTError, Exception):
        return None


def require_user(request: Request, token: Optional[str] = None):
    """Require authenticated user — raises 401 if not logged in."""
    user = get_current_user(request, token)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def signup_user(email: str, username: str, password: str, full_name: str = "") -> dict:
    """Create a new user."""
    db = SessionLocal()
    try:
        if db.query(User).filter(User.email == email).first():
            return {"error": "Email already registered"}
        if db.query(User).filter(User.username == username).first():
            return {"error": "Username already taken"}

        user = User(
            email=email, username=username,
            hashed_password=hash_password(password),
        )
        db.add(user)
        db.flush()

        # Create default portfolio
        portfolio = Portfolio(user_id=user.id, value=0.0, cash=0.0)
        db.add(portfolio)
        db.commit()

        token = create_token({"sub": str(user.id), "email": email})
        return {"token": token, "user_id": user.id, "username": username}
    except Exception as e:
        db.rollback()
        return {"error": str(e)}
    finally:
        db.close()


def login_user(email: str, password: str) -> dict:
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        if not user or not verify_password(password, user.hashed_password):
            return {"error": "Invalid email or password"}
        token = create_token({"sub": str(user.id), "email": email})
        return {"token": token, "user_id": user.id, "username": user.username}
    finally:
        db.close()


def deposit_funds(user_id: int, amount: float) -> dict:
    """Add funds to user's virtual balance."""
    if amount <= 0:
        return {"error": "Amount must be positive"}
    if amount > 10000:
        return {"error": "Max deposit is $10,000 per transaction"}
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
