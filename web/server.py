"""Ferchaud Web Server — FastAPI + Jinja2"""
from __future__ import annotations
import os, sys, json
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, Depends, HTTPException, Body
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from web.auth import get_current_user, require_user, signup_user, login_user, deposit_funds
from web.database import SessionLocal, User, Portfolio, Trade, Deposit, TIERS
from web.stripe_handler import (
    create_customer, create_checkout_session, create_billing_portal,
    create_deposit_intent, verify_webhook
)

app = FastAPI(title="Ferchaud", docs_url=None, redoc_url=None)

BASE_DIR = Path(__file__).resolve().parent
try:
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
except Exception:
    pass  # Static files served by CDN on Vercel

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# ─── Helpers ──────────────────────────────────────────────────────────────────

def get_broker_data() -> dict:
    try:
        from core.broker import Broker
        broker = Broker()
        acct = broker.get_account()
        positions = broker.get_positions()
        orders = broker.get_open_orders()
        return {
            "equity": float(acct.get("equity", 0)),
            "cash": float(acct.get("cash", 0)),
            "positions": positions,
            "orders": orders,
        }
    except Exception as e:
        return {"equity": 0, "cash": 0, "positions": {}, "orders": [], "error": str(e)}


def get_scanner_results() -> dict:
    try:
        from data.full_market_scanner import scanner
        from data.x_research import x_researcher
        return {
            "hot": scanner.hot_details[:15] if hasattr(scanner, "hot_details") else [],
            "trending": x_researcher.trending_details[:10] if hasattr(x_researcher, "trending_details") else [],
        }
    except Exception:
        return {"hot": [], "trending": []}


# ─── Page Routes ──────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse("/dashboard")
    return templates.TemplateResponse("landing.html", {"request": request})


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("auth.html", {"request": request, "mode": "login"})


@app.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request):
    return templates.TemplateResponse("auth.html", {"request": request, "mode": "signup"})


@app.get("/onboarding", response_class=HTMLResponse)
async def onboarding_page(request: Request):
    user = require_user(request)
    return templates.TemplateResponse("onboarding.html", {"request": request, "user": user})


@app.get("/pricing", response_class=HTMLResponse)
async def pricing_page(request: Request):
    return templates.TemplateResponse("pricing.html", {"request": request})


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = require_user(request)
    if not user.onboarding_complete:
        return RedirectResponse("/onboarding")
    db = SessionLocal()
    try:
        trades = db.query(Trade).filter(Trade.user_id == user.id).order_by(Trade.timestamp.desc()).limit(50).all()
        deposits = db.query(Deposit).filter(Deposit.user_id == user.id).all()
        total_deposited = sum(d.amount for d in deposits)
        tier_info = TIERS.get(user.subscription_tier or "free", TIERS["free"])
        broker = get_broker_data()
        scanner = get_scanner_results()
        return templates.TemplateResponse("dashboard.html", {
            "request": request,
            "user": user,
            "trades": trades,
            "total_deposited": total_deposited,
            "broker": broker,
            "scanner": scanner,
            "tier_info": tier_info,
            "tiers": TIERS,
        })
    finally:
        db.close()


# ─── Auth APIs ────────────────────────────────────────────────────────────────

@app.post("/api/auth/signup")
async def api_signup(request: Request, body: dict = Body(...)):
    email    = body.get("email", "").strip().lower()
    username = body.get("username", "").strip()
    password = body.get("password", "")
    if not email or not username or not password:
        raise HTTPException(400, "All fields required")
    result = signup_user(email, username, password)
    response = JSONResponse(result)
    response.set_cookie("token", result["token"], httponly=True, max_age=86400 * 30, samesite="lax")
    return response


@app.post("/api/auth/login")
async def api_login(request: Request, body: dict = Body(...)):
    email    = body.get("email", "").strip().lower()
    password = body.get("password", "")
    result   = login_user(email, password)
    response = JSONResponse(result)
    response.set_cookie("token", result["token"], httponly=True, max_age=86400 * 30, samesite="lax")
    return response


@app.post("/api/auth/logout")
async def api_logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie("token")
    return response


# ─── Bot Control APIs ─────────────────────────────────────────────────────────

@app.post("/api/bot/start")
async def bot_start(request: Request, body: dict = Body(...)):
    user = require_user(request)
    mode = body.get("mode", user.trading_mode or "passive")
    try:
        from web.bot_manager import start_bot
        started = start_bot(user.id, mode)
        return {"ok": True, "started": started, "mode": mode}
    except Exception as e:
        raise HTTPException(500, f"Bot start failed: {e}")


@app.post("/api/bot/stop")
async def bot_stop(request: Request):
    user = require_user(request)
    try:
        from web.bot_manager import stop_bot
        stop_bot(user.id)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, f"Bot stop failed: {e}")


@app.post("/api/bot/mode")
async def bot_mode(request: Request, body: dict = Body(...)):
    user = require_user(request)
    mode = body.get("mode", "passive")
    if mode not in ("passive", "aggressive"):
        raise HTTPException(400, "Mode must be passive or aggressive")
    try:
        from web.bot_manager import set_mode
        set_mode(user.id, mode)
        return {"ok": True, "mode": mode}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/bot/status")
async def bot_status(request: Request):
    user = require_user(request)
    try:
        from web.bot_manager import get_status
        return get_status(user.id)
    except Exception:
        db = SessionLocal()
        try:
            u = db.query(User).filter(User.id == user.id).first()
            return {"running": u.bot_enabled or False, "mode": u.trading_mode or "passive"}
        finally:
            db.close()


# ─── Onboarding APIs ──────────────────────────────────────────────────────────

@app.post("/api/onboarding/tier")
async def onboarding_tier(request: Request, body: dict = Body(...)):
    user = require_user(request)
    tier = body.get("tier", "free")
    if tier not in TIERS:
        raise HTTPException(400, "Invalid tier")
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.id == user.id).first()
        u.subscription_tier  = tier
        u.revenue_share_pct  = TIERS[tier]["revenue_share"]
        u.onboarding_step    = max(u.onboarding_step or 0, 1)
        db.commit()
    finally:
        db.close()
    return {"ok": True}


@app.post("/api/onboarding/wallet")
async def onboarding_wallet(request: Request, body: dict = Body(...)):
    user = require_user(request)
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.id == user.id).first()
        u.wallet_address  = body.get("wallet_address")
        u.wallet_type     = body.get("wallet_type")
        u.onboarding_step = max(u.onboarding_step or 0, 2)
        db.commit()
    finally:
        db.close()
    return {"ok": True}


@app.post("/api/onboarding/complete")
async def onboarding_complete(request: Request):
    user = require_user(request)
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.id == user.id).first()
        u.onboarding_complete = True
        u.onboarding_step     = 4
        db.commit()
    finally:
        db.close()
    return {"ok": True}


# ─── Deposit API ──────────────────────────────────────────────────────────────

@app.post("/api/deposit")
async def api_deposit(request: Request, body: dict = Body(...)):
    user   = require_user(request)
    amount = float(body.get("amount", 0))
    if amount < 50:
        raise HTTPException(400, "Minimum deposit $50")
    result = deposit_funds(user.id, amount)
    return result


# ─── Subscription APIs ────────────────────────────────────────────────────────

@app.post("/api/subscription/checkout")
async def subscription_checkout(request: Request, body: dict = Body(...)):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "not_logged_in"})
    tier = body.get("tier", "pro")
    if tier not in TIERS or not TIERS[tier]["stripe_price_id"]:
        raise HTTPException(400, "Invalid tier")
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.id == user.id).first()
        if not u.stripe_customer_id:
            cid = create_customer(u.email, u.username)
            if cid:
                u.stripe_customer_id = cid
                db.commit()
        price_id = TIERS[tier]["stripe_price_id"]
        base = str(request.base_url).rstrip("/")
        url  = create_checkout_session(
            u.stripe_customer_id or "", price_id,
            success_url=f"{base}/dashboard?subscribed=1",
            cancel_url=f"{base}/pricing",
        )
        return {"url": url or f"{base}/pricing?error=stripe"}
    finally:
        db.close()


@app.get("/api/subscription/portal")
async def subscription_portal(request: Request):
    user = require_user(request)
    db   = SessionLocal()
    try:
        u = db.query(User).filter(User.id == user.id).first()
        if not u.stripe_customer_id:
            raise HTTPException(400, "No subscription found")
        base = str(request.base_url).rstrip("/")
        url  = create_billing_portal(u.stripe_customer_id, f"{base}/dashboard")
        return RedirectResponse(url or "/dashboard")
    finally:
        db.close()


@app.post("/api/subscription/webhook")
async def subscription_webhook(request: Request):
    payload = await request.body()
    sig     = request.headers.get("stripe-signature", "")
    event   = verify_webhook(payload, sig)
    if not event:
        raise HTTPException(400, "Invalid signature")
    etype = event.get("type", "")
    data  = event.get("data", {}).get("object", {})
    if etype in ("customer.subscription.created", "customer.subscription.updated"):
        cid    = data.get("customer")
        status = data.get("status")
        sub_id = data.get("id")
        db = SessionLocal()
        try:
            u = db.query(User).filter(User.stripe_customer_id == cid).first()
            if u:
                u.stripe_sub_id       = sub_id
                u.subscription_status = status
                db.commit()
        finally:
            db.close()
    return {"received": True}


# ─── Data APIs ────────────────────────────────────────────────────────────────

@app.get("/api/portfolio")
async def api_portfolio(request: Request):
    user   = require_user(request)
    broker = get_broker_data()
    db     = SessionLocal()
    try:
        u        = db.query(User).filter(User.id == user.id).first()
        deposits = db.query(Deposit).filter(Deposit.user_id == user.id).all()
        total_deposited = sum(d.amount for d in deposits)
        history  = db.query(Portfolio).filter(Portfolio.user_id == user.id).order_by(Portfolio.timestamp.asc()).limit(200).all()
        tier_info = TIERS.get(u.subscription_tier or "free", TIERS["free"])
        return {
            "equity":            broker["equity"],
            "cash":              broker["cash"],
            "positions":         broker["positions"],
            "total_deposited":   total_deposited,
            "balance":           u.virtual_balance,
            "gross_profit":      u.gross_profit,
            "platform_cut":      u.platform_cut,
            "net_profit":        u.net_profit,
            "revenue_share":     tier_info["revenue_share"],
            "subscription_tier": u.subscription_tier or "free",
            "wallet_address":    u.wallet_address,
            "bot_enabled":       u.bot_enabled or False,
            "trading_mode":      u.trading_mode or "passive",
            "history": [{"t": p.timestamp.isoformat(), "v": p.value} for p in history],
        }
    finally:
        db.close()


@app.get("/api/market")
async def api_market(request: Request):
    require_user(request)
    return get_scanner_results()


@app.get("/api/trades")
async def api_trades(request: Request):
    user = require_user(request)
    db   = SessionLocal()
    try:
        trades = db.query(Trade).filter(Trade.user_id == user.id).order_by(Trade.timestamp.desc()).limit(100).all()
        return [
            {"id": t.id, "symbol": t.symbol, "side": t.side, "qty": t.qty,
             "price": t.price, "pnl": t.pnl, "strategy": t.strategy,
             "timestamp": t.timestamp.isoformat()}
            for t in trades
        ]
    finally:
        db.close()


# ─── Feed & Sources Pages ─────────────────────────────────────────────────────

@app.get("/feed", response_class=HTMLResponse)
async def feed_page(request: Request):
    user = require_user(request)
    return templates.TemplateResponse("feed.html", {"request": request, "user": user})


@app.get("/sources", response_class=HTMLResponse)
async def sources_page(request: Request):
    return templates.TemplateResponse("sources.html", {"request": request})


@app.get("/api/feed")
async def api_feed(request: Request):
    """Return recent bot activity for the feed."""
    user = require_user(request)
    db = SessionLocal()
    try:
        # Get recent trades
        trades = db.query(Trade).filter(Trade.user_id == user.id).order_by(Trade.timestamp.desc()).limit(30).all()

        feed_items = []
        for t in trades:
            feed_items.append({
                "type": "buy" if t.side == "buy" else "sell",
                "symbol": t.symbol,
                "qty": t.qty,
                "price": t.price,
                "pnl": t.pnl,
                "strategy": t.strategy,
                "timestamp": t.timestamp.isoformat(),
            })

        # Add scanner results as signal items
        try:
            from data.full_market_scanner import scanner
            if hasattr(scanner, 'hot_details'):
                for h in scanner.hot_details[:5]:
                    feed_items.append({
                        "type": "signal",
                        "symbol": h.get("symbol", ""),
                        "detail": f"{h.get('change_pct', 0):.1f}% move, {h.get('trade_count', 0)} trades",
                        "timestamp": None,
                    })
        except Exception:
            pass

        # Add X research as research items
        try:
            from data.x_research import x_researcher
            if hasattr(x_researcher, 'trending_details'):
                for r in x_researcher.trending_details[:3]:
                    feed_items.append({
                        "type": "research",
                        "symbol": r.get("ticker", ""),
                        "detail": r.get("catalyst", "Trending on X"),
                        "score": r.get("score", 0),
                        "timestamp": None,
                    })
        except Exception:
            pass

        return feed_items
    finally:
        db.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("web.server:app", host="0.0.0.0", port=3000, reload=True)
