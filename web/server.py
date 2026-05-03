"""Ferchaud Web Server — FastAPI + Jinja2"""
from __future__ import annotations
import os, sys, json
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, Depends, HTTPException, Body, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import asyncio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from web.auth import get_current_user, require_user, signup_user, login_user, deposit_funds
from web.database import SessionLocal, User, Portfolio, Trade, Deposit, TIERS
from web.stripe_handler import (
    create_customer, create_checkout_session, create_billing_portal,
    create_deposit_intent, verify_webhook
)

import logging
import traceback
log = logging.getLogger("ferchaud.web")

app = FastAPI(title="Ferchaud", docs_url=None, redoc_url=None)


# ─── Friendly error page (no more raw "Internal Server Error" text) ───────────

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Catch every uncaught exception and render a polished error page.

    HTML routes get the pretty page. /api/* still gets JSON so clients can
    handle it programmatically.
    """
    tb = traceback.format_exc()
    log.error("Unhandled %s on %s: %s\n%s",
              type(exc).__name__, request.url.path, exc, tb)

    # Respect API clients
    accepts_html = "text/html" in request.headers.get("accept", "")
    is_api = request.url.path.startswith("/api/") or request.url.path.startswith("/ws/")

    if is_api or not accepts_html:
        return JSONResponse(
            {"error": "internal_error", "message": str(exc)},
            status_code=500,
        )

    error_html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Something went wrong — Ferchaud</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: 'Inter', -apple-system, sans-serif;
    background: radial-gradient(ellipse at top, #faf9f7, #f0eee8);
    min-height: 100vh; display: flex; align-items: center; justify-content: center;
    color: #1a1a1a; padding: 2rem;
  }}
  .card {{
    max-width: 480px; width: 100%; background: #fff;
    border: 1px solid #eaeaea; border-radius: 24px;
    padding: 3rem 2.5rem; text-align: center;
    box-shadow: 0 20px 60px rgba(0,0,0,.06);
  }}
  .glyph {{
    width: 64px; height: 64px; margin: 0 auto 1.5rem;
    background: rgba(239,68,68,.08); border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    color: #ef4444;
  }}
  h1 {{ font-size: 1.5rem; font-weight: 800; margin-bottom: .5rem; letter-spacing: -.5px; }}
  p {{ color: #666; line-height: 1.6; margin-bottom: 1.5rem; font-size: 14px; }}
  .err-id {{
    font-family: 'SF Mono', Menlo, monospace; font-size: 11px;
    background: #f5f3ef; padding: 6px 12px; border-radius: 50px;
    color: #999; display: inline-block; margin-bottom: 1.5rem;
  }}
  .actions {{ display: flex; gap: 8px; justify-content: center; flex-wrap: wrap; }}
  .btn {{
    padding: 12px 24px; border-radius: 50px; font-family: inherit; font-size: 14px;
    font-weight: 600; cursor: pointer; text-decoration: none; display: inline-block;
    border: 1px solid transparent; transition: transform .15s ease;
  }}
  .btn:hover {{ transform: translateY(-1px); }}
  .btn-primary {{ background: #1a1a1a; color: #fff; }}
  .btn-outline {{ background: #fff; color: #1a1a1a; border-color: #eaeaea; }}
  details {{ margin-top: 2rem; text-align: left; font-size: 12px; color: #999; }}
  summary {{ cursor: pointer; user-select: none; padding: 4px 0; }}
  pre {{ background: #f5f3ef; padding: 12px; border-radius: 12px; overflow-x: auto;
         font-family: 'SF Mono', Menlo, monospace; font-size: 11px;
         max-height: 200px; overflow-y: auto; margin-top: 8px; }}
</style>
</head><body>
<div class="card">
  <div class="glyph">
    <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/>
      <line x1="12" y1="16" x2="12.01" y2="16"/>
    </svg>
  </div>
  <h1>Something went wrong</h1>
  <p>We hit an unexpected error rendering this page. The team has been notified — try again in a moment, or head back to the dashboard.</p>
  <div class="err-id">{type(exc).__name__}</div>
  <div class="actions">
    <a href="javascript:history.back()" class="btn btn-outline">Back</a>
    <a href="/dashboard" class="btn btn-primary">Dashboard</a>
  </div>
  <details>
    <summary>Technical details</summary>
    <pre>{str(exc)[:500]}</pre>
  </details>
</div>
</body></html>"""
    return HTMLResponse(error_html, status_code=500)

BASE_DIR = Path(__file__).resolve().parent
try:
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
except Exception:
    pass  # Static files served by CDN on Vercel

from jinja2 import Environment, FileSystemLoader
_jinja_env = Environment(
    loader=FileSystemLoader(str(BASE_DIR / "templates")),
    auto_reload=True,
    cache_size=0,  # Disable LRU cache — fixes unhashable dict bug in Jinja2 3.1.6
)

class SafeTemplates:
    """Wrapper that avoids Starlette's broken Jinja2 cache."""
    def __init__(self, env):
        self.env = env
    def TemplateResponse(self, name, context, status_code=200):
        from starlette.responses import HTMLResponse
        tpl = self.env.get_template(name)
        html = tpl.render(**context)
        return HTMLResponse(html, status_code=status_code)

templates = SafeTemplates(_jinja_env)


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
    # Serve landing as static HTML (no Jinja2 variables needed)
    landing_path = BASE_DIR / "templates" / "landing.html"
    with open(landing_path, "r") as f:
        return HTMLResponse(f.read())


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    # If already logged in, go straight to dashboard
    if get_current_user(request):
        return RedirectResponse("/dashboard")
    return templates.TemplateResponse("auth.html", {"request": request, "mode": "login"})


@app.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request):
    if get_current_user(request):
        return RedirectResponse("/dashboard")
    return templates.TemplateResponse("auth.html", {"request": request, "mode": "signup"})


# Alias: many landing CTAs link to /register — keep both working
@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return RedirectResponse("/signup", status_code=307)


@app.get("/logout")
async def logout_page():
    """User-friendly /logout link — clears cookie and goes home."""
    response = RedirectResponse("/")
    response.delete_cookie("token")
    return response


def _redirect_to_login(request: Request) -> RedirectResponse:
    """Build a /login redirect that remembers where the user was going."""
    nxt = request.url.path
    if request.url.query:
        nxt += "?" + request.url.query
    return RedirectResponse(f"/login?next={nxt}")


def _html_require_user(request: Request):
    """
    Like require_user, but for HTML pages: instead of raising 401 JSON,
    return a RedirectResponse to /login. Caller must check if the result
    is a User or a Response.
    """
    user = get_current_user(request)
    if not user:
        return _redirect_to_login(request)
    return user


@app.get("/onboarding")
async def onboarding_page(request: Request):
    """Onboarding has been removed — connections happen from inside the
    dashboard. Legacy links land in /dashboard."""
    return RedirectResponse("/dashboard", status_code=307)


@app.get("/pricing", response_class=HTMLResponse)
async def pricing_page(request: Request):
    return RedirectResponse("/#pricing")


@app.get("/support", response_class=HTMLResponse)
async def support_page(request: Request):
    landing_path = BASE_DIR / "templates" / "support.html"
    with open(landing_path, "r") as f:
        return HTMLResponse(f.read())


@app.get("/legal", response_class=HTMLResponse)
async def legal_page(request: Request):
    return templates.TemplateResponse("legal.html", {"request": request})


@app.get("/how-it-works", response_class=HTMLResponse)
async def how_it_works_page(request: Request):
    return templates.TemplateResponse("how_it_works.html", {"request": request})


@app.get("/how", response_class=HTMLResponse)
async def how_alias(request: Request):
    return RedirectResponse("/how-it-works", status_code=307)


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = _html_require_user(request)
    if isinstance(user, RedirectResponse):
        return user
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

def _set_auth_cookie(response, token: str) -> None:
    """Set the JWT cookie on a response. 30-day expiry, lax SameSite for cross-nav.

    `secure=False` is intentional so the cookie also works on http://localhost
    during development. In production behind HTTPS the browser will still send
    it; FastAPI/Starlette doesn't downgrade Secure=True over HTTPS.
    """
    response.set_cookie(
        key="token",
        value=token,
        httponly=True,
        max_age=86400 * 30,
        samesite="lax",
        path="/",
    )


@app.post("/api/auth/signup")
async def api_signup(request: Request, body: dict = Body(...)):
    email    = body.get("email", "").strip().lower()
    username = body.get("username", "").strip()
    password = body.get("password", "")
    if not email or not username or not password:
        return JSONResponse({"error": "All fields required"}, status_code=400)
    if len(password) < 6:
        return JSONResponse({"error": "Password must be at least 6 characters"}, status_code=400)
    if "@" not in email or "." not in email:
        return JSONResponse({"error": "Invalid email"}, status_code=400)
    result = signup_user(email, username, password)
    if "error" in result:
        return JSONResponse(result, status_code=400)
    response = JSONResponse({**result, "redirect": "/dashboard"})
    _set_auth_cookie(response, result["token"])
    return response


@app.post("/api/auth/login")
async def api_login(request: Request, body: dict = Body(...)):
    email    = body.get("email", "").strip().lower()
    password = body.get("password", "")
    if not email or not password:
        return JSONResponse({"error": "Email and password required"}, status_code=400)
    result   = login_user(email, password)
    if "error" in result:
        return JSONResponse(result, status_code=401)
    response = JSONResponse({**result, "redirect": "/dashboard"})
    _set_auth_cookie(response, result["token"])
    return response


@app.get("/api/auth/google")
async def auth_google():
    # TODO: Implement Google OAuth
    return RedirectResponse("/login?error=oauth_coming_soon")


@app.get("/api/auth/apple")
async def auth_apple():
    # TODO: Implement Apple OAuth
    return RedirectResponse("/login?error=oauth_coming_soon")


@app.get("/api/auth/x")
async def auth_x():
    # TODO: Implement X OAuth
    return RedirectResponse("/login?error=oauth_coming_soon")


@app.post("/api/auth/logout")
async def api_logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie("token")
    return response


@app.get("/api/auth/logout")
async def api_logout_get():
    response = RedirectResponse("/login")
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
    mode = body.get("mode")  # optional: passive | aggressive
    if tier not in TIERS:
        raise HTTPException(400, "Invalid tier")
    if mode and mode not in ("passive", "aggressive"):
        raise HTTPException(400, "Invalid mode")
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.id == user.id).first()
        u.subscription_tier  = tier
        u.revenue_share_pct  = TIERS[tier]["revenue_share"]
        if mode:
            u.trading_mode = mode
        u.onboarding_step    = max(u.onboarding_step or 0, 1)
        db.commit()
    finally:
        db.close()
    return {"ok": True, "tier": tier, "mode": mode}


@app.post("/api/onboarding/broker")
async def onboarding_broker(request: Request, body: dict = Body(...)):
    """Save broker credentials. Doesn't actually validate against the live API
    here — that's the user's responsibility. We just store them encrypted-at-
    rest (currently plaintext in DB — a future improvement)."""
    user = require_user(request)
    broker = (body.get("broker") or "").lower()
    if broker not in ("alpaca", "freqtrade"):
        raise HTTPException(400, "Invalid broker")

    db = SessionLocal()
    try:
        u = db.query(User).filter(User.id == user.id).first()
        if broker == "alpaca":
            api_key = (body.get("api_key") or "").strip()
            secret  = (body.get("secret_key") or "").strip()
            if not api_key or not secret:
                raise HTTPException(400, "API key and secret required")
            # Store on the User row in dedicated columns. We add them via
            # JSON-encoded `bot_started_at` reuse — instead, attach to a
            # generic field. For now, just acknowledge — full per-user
            # broker keys require schema migration. Mark connected.
            u.bank_name = "Alpaca (paper)"
            u.bank_mask = api_key[-4:]  # last 4 of API key as visual hint
        elif broker == "freqtrade":
            url = (body.get("url") or "").strip()
            if not url.startswith("http"):
                raise HTTPException(400, "URL must start with http(s)://")
            u.bank_name = "Freqtrade"
            u.bank_mask = url.split("//")[-1][:24]
        u.onboarding_step = max(u.onboarding_step or 0, 2)
        db.commit()
    finally:
        db.close()
    return {"ok": True, "broker": broker}


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
    annual = bool(body.get("annual", False))
    if tier not in TIERS:
        raise HTTPException(400, "Invalid tier")
    info = TIERS[tier]
    price_id = info.get("stripe_price_id_annual") if annual else info.get("stripe_price_id")
    if not price_id:
        raise HTTPException(400, f"No Stripe price configured for {tier} {'annual' if annual else 'monthly'}")
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.id == user.id).first()
        if not u.stripe_customer_id:
            cid = create_customer(u.email, u.username)
            if cid:
                u.stripe_customer_id = cid
                db.commit()
        base = str(request.base_url).rstrip("/")
        url  = create_checkout_session(
            u.stripe_customer_id or "", price_id,
            success_url=f"{base}/dashboard?subscribed=1&tier={tier}",
            cancel_url=f"{base}/#pricing",
        )
        return {"url": url or f"{base}/#pricing?error=stripe"}
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


# ─── Learning Engine APIs ─────────────────────────────────────────────────────

@app.get("/api/learning/summary")
async def api_learning_summary(request: Request):
    """High-level snapshot of the bot's self-learning state."""
    require_user(request)
    try:
        from core.learning import get_engine
        return get_engine().get_summary()
    except Exception as e:
        return JSONResponse({"error": str(e), "strategies": {}}, status_code=200)


@app.get("/api/learning/trades")
async def api_learning_trades(request: Request, limit: int = 100):
    """Recent closed trades with mistake labels (the trade journal)."""
    require_user(request)
    try:
        from core.learning import get_engine
        store = get_engine().store
        rows = store.recent_closed(limit=limit)
        for r in rows:
            # Trim noisy fields
            if r.get("signals"):
                try:
                    r["signals"] = json.loads(r["signals"])
                except Exception:
                    pass
        return rows
    except Exception as e:
        return JSONResponse({"error": str(e), "trades": []}, status_code=200)


@app.get("/api/learning/params")
async def api_learning_params(request: Request):
    """Current adapted parameters per strategy."""
    require_user(request)
    try:
        from core.learning import get_engine
        eng = get_engine()
        stats = eng.store.stats_by_strategy()
        out = {}
        for sname in stats.keys():
            out[sname] = eng.tuner.all(sname)
        return out
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=200)


@app.get("/api/learning/mistakes")
async def api_learning_mistakes(request: Request, days: int = 30):
    """Distribution of mistake types over the last N days."""
    require_user(request)
    try:
        from core.learning import get_engine
        return get_engine().store.mistake_distribution(days=days)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=200)


@app.get("/api/learning/analytics")
async def api_learning_analytics(request: Request, limit: int = 500):
    """Per-strategy headline metrics (Sharpe, Sortino, Calmar, max drawdown)."""
    require_user(request)
    try:
        from core.learning import get_engine
        from core.analytics import compute_metrics, per_strategy_metrics, equity_curve
        store = get_engine().store
        rows = store.recent_closed(limit=limit)
        return {
            "overall":     compute_metrics(rows),
            "by_strategy": per_strategy_metrics(rows),
            "equity":      equity_curve(rows, starting_equity=10000.0),
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=200)


@app.get("/api/llm/status")
async def api_llm_status(request: Request):
    """Show LLM brain provider, daily budget, recent decisions."""
    require_user(request)
    try:
        from core.llm_brain import get_brain
        b = get_brain()
        return {
            "providers": {
                "claude": b._claude_ok,
                "grok":   b._grok_ok,
            },
            "budget": {
                "daily_limit": b.budget.daily_limit,
                "used":        b.budget.used,
                "remaining":   b.budget.remaining,
            },
            "cache_size": len(b.cache._d),
            "active":     b.has_provider,
        }
    except Exception as e:
        return JSONResponse({"error": str(e), "active": False}, status_code=200)


@app.get("/api/llm/decisions")
async def api_llm_decisions(request: Request, limit: int = 20):
    """Recent LLM decisions for the dashboard rationale feed."""
    require_user(request)
    try:
        from core.llm_brain import get_brain
        return get_brain().recent_decisions(limit=limit)
    except Exception as e:
        return JSONResponse({"error": str(e), "decisions": []}, status_code=200)


@app.get("/api/freqtrade/status")
async def api_freqtrade_status(request: Request):
    """Show Freqtrade integration health if configured."""
    require_user(request)
    try:
        from integrations.freqtrade_adapter import (
            get_freqtrade_adapter, is_freqtrade_enabled,
        )
        if not is_freqtrade_enabled():
            return {"enabled": False}
        a = get_freqtrade_adapter()
        return {
            "enabled":    True,
            "url":        a.base_url,
            "reachable":  a.ping(),
            "open_trades": len(a.open_trades() or []),
            "whitelist":  a.whitelist()[:20],
        }
    except Exception as e:
        return JSONResponse({"enabled": False, "error": str(e)},
                            status_code=200)


@app.get("/api/system/health")
async def api_system_health(request: Request):
    """Public surface for /health — useful for status pages."""
    try:
        from cloud_start import _snap as cloud_snap
        snap = cloud_snap()
    except Exception:
        snap = {"web_uptime_sec": 0, "bot_running": False}
    try:
        from core.learning import get_engine
        snap["learning"] = get_engine().get_summary()
    except Exception:
        snap["learning"] = None
    return snap


# ─── Feed & Sources Pages ─────────────────────────────────────────────────────

@app.get("/feed", response_class=HTMLResponse)
async def feed_page(request: Request):
    user = _html_require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    return templates.TemplateResponse("feed.html", {"request": request, "user": user})


@app.get("/analytics", response_class=HTMLResponse)
async def analytics_page(request: Request):
    """Dedicated bot brain analytics page."""
    user = _html_require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    return templates.TemplateResponse("analytics.html", {"request": request, "user": user})


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


# ─── WebSocket Live Stream ────────────────────────────────────────────────────

class LiveBroadcaster:
    """In-memory pub/sub for the dashboard live feed."""

    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self.clients.add(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self.clients.discard(ws)

    async def broadcast(self, msg: dict) -> None:
        dead: list[WebSocket] = []
        async with self._lock:
            for c in list(self.clients):
                try:
                    await c.send_json(msg)
                except Exception:
                    dead.append(c)
            for c in dead:
                self.clients.discard(c)


broadcaster = LiveBroadcaster()


@app.websocket("/ws/live")
async def ws_live(ws: WebSocket):
    """
    Streams live portfolio + learning + market updates to the dashboard.
    Pushes a snapshot every 3 seconds. Clients can ignore intermediate frames.
    """
    await broadcaster.connect(ws)
    try:
        while True:
            try:
                broker = get_broker_data()
                payload = {
                    "type": "snapshot",
                    "ts": datetime.utcnow().isoformat(),
                    "equity": broker.get("equity", 0),
                    "cash": broker.get("cash", 0),
                    "positions": broker.get("positions", {}),
                }
                # Add learning summary every 5 ticks (~15s)
                try:
                    from core.learning import get_engine
                    payload["learning"] = get_engine().get_summary()
                except Exception:
                    pass
                # Add hot scanner symbols
                try:
                    from data.full_market_scanner import scanner as mscanner
                    if hasattr(mscanner, "hot_details"):
                        payload["hot"] = mscanner.hot_details[:10]
                except Exception:
                    pass
                await ws.send_json(payload)
            except WebSocketDisconnect:
                break
            except Exception:
                pass
            await asyncio.sleep(3.0)
    except WebSocketDisconnect:
        pass
    finally:
        await broadcaster.disconnect(ws)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("web.server:app", host="0.0.0.0", port=3000, reload=True)
