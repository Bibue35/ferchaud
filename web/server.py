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
from web.supabase_client import SUPABASE_URL

app = FastAPI(title="Ferchaud", docs_url=None, redoc_url=None)

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Redirect to /login on 401 for page routes; return JSON for API routes."""
    if exc.status_code == 401:
        path = request.url.path
        if path.startswith("/api/"):
            return JSONResponse({"error": "Not authenticated"}, status_code=401)
        return RedirectResponse(f"/login?next={path}", status_code=302)
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


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
    return RedirectResponse("/#pricing")


@app.get("/support", response_class=HTMLResponse)
async def support_page(request: Request):
    landing_path = BASE_DIR / "templates" / "support.html"
    with open(landing_path, "r") as f:
        return HTMLResponse(f.read())


@app.get("/legal", response_class=HTMLResponse)
async def legal_page(request: Request):
    return templates.TemplateResponse("legal.html", {"request": request})


# ─── Strategy Vault ───────────────────────────────────────────────────────────

@app.get("/vault", response_class=HTMLResponse)
async def vault_page(request: Request):
    """Strategy Vault — Pro gets 10 equity strategies, Elite gets all 151."""
    from web.strategy_data import STRATEGIES, PRO_STRATEGIES, STATUS_LABELS
    import json as _json
    user = require_user(request)
    if not user:
        return RedirectResponse("/login")

    tier = (user.subscription_tier or "free").lower()
    if tier not in ("pro", "elite"):
        return RedirectResponse("/pricing?reason=vault")

    # Check if user has accepted the risk disclosure
    vault_cookie = request.cookies.get("vault_accepted")
    if vault_cookie != "1":
        return RedirectResponse(f"/vault/legal")

    # Select strategies by tier
    if tier == "pro":
        strategies = PRO_STRATEGIES
    else:
        strategies = STRATEGIES  # All 151

    live_count = sum(1 for s in strategies if s.get("status") == "live")

    # Serialize strategies to JSON for client-side JS (safe — no raw HTML injection)
    strategies_json = _json.dumps(strategies, ensure_ascii=False)

    return templates.TemplateResponse("vault.html", {
        "request": request,
        "user": user,
        "tier": tier,
        "strategies": strategies,
        "strategies_json": strategies_json,
        "live_count": live_count,
    })


@app.get("/vault/legal", response_class=HTMLResponse)
async def vault_legal_page(request: Request):
    """Risk disclosure scroll-through before accessing vault."""
    user = require_user(request)
    if not user:
        return RedirectResponse("/login")
    tier = (user.subscription_tier or "free").lower()
    if tier not in ("pro", "elite"):
        return RedirectResponse("/pricing?reason=vault")
    return templates.TemplateResponse("risk_disclosure.html", {
        "request": request,
        "user": user,
        "tier": tier,
    })


@app.post("/vault/accept")
async def vault_accept(request: Request):
    """Set the vault_accepted cookie and redirect to vault."""
    user = require_user(request)
    if not user:
        return RedirectResponse("/login")
    response = RedirectResponse("/vault", status_code=303)
    # 30-day cookie — user must re-accept after 30 days
    response.set_cookie("vault_accepted", "1", max_age=86400 * 30, httponly=True, samesite="lax")
    return response


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


# ─── Auth APIs (Supabase) ────────────────────────────────────────────────────

def _set_auth_cookies(response, access_token: str, refresh_token: str):
    """Set Supabase auth cookies on a response."""
    max_age = 86400 * 30  # 30 days
    response.set_cookie("sb-access-token", access_token, httponly=True, max_age=max_age, samesite="lax")
    response.set_cookie("sb-refresh-token", refresh_token, httponly=True, max_age=max_age, samesite="lax")


def _delete_auth_cookies(response):
    """Delete Supabase auth cookies."""
    response.delete_cookie("sb-access-token")
    response.delete_cookie("sb-refresh-token")
    # Also clean up legacy cookie
    response.delete_cookie("token")


@app.post("/api/auth/signup")
async def api_signup(request: Request, body: dict = Body(...)):
    email    = body.get("email", "").strip().lower()
    username = body.get("username", "").strip()
    password = body.get("password", "")
    if not email or not username or not password:
        return JSONResponse({"error": "All fields required"}, status_code=400)
    result = signup_user(email, username, password)
    if "error" in result:
        return JSONResponse(result, status_code=400)
    response = JSONResponse(result)
    if "access_token" in result:
        _set_auth_cookies(response, result["access_token"], result["refresh_token"])
    return response


@app.post("/api/auth/login")
async def api_login(request: Request, body: dict = Body(...)):
    email    = body.get("email", "").strip().lower()
    password = body.get("password", "")
    result   = login_user(email, password)
    if "error" in result:
        return JSONResponse(result, status_code=401)
    response = JSONResponse(result)
    if "access_token" in result:
        _set_auth_cookies(response, result["access_token"], result["refresh_token"])
    return response


# ─── OAuth via Supabase ───────────────────────────────────────────────────────

def _get_site_url(request: Request) -> str:
    """Get the site base URL for OAuth redirect_to."""
    return str(request.base_url).rstrip("/")


@app.get("/api/auth/google")
async def auth_google(request: Request):
    """Redirect to Supabase Google OAuth."""
    site_url = _get_site_url(request)
    redirect_url = (
        f"{SUPABASE_URL}/auth/v1/authorize"
        f"?provider=google"
        f"&redirect_to={site_url}/api/auth/callback"
    )
    return RedirectResponse(redirect_url)


@app.get("/api/auth/apple")
async def auth_apple(request: Request):
    """Redirect to Supabase Apple OAuth."""
    site_url = _get_site_url(request)
    redirect_url = (
        f"{SUPABASE_URL}/auth/v1/authorize"
        f"?provider=apple"
        f"&redirect_to={site_url}/api/auth/callback"
    )
    return RedirectResponse(redirect_url)


@app.get("/api/auth/x")
async def auth_x(request: Request):
    """Redirect to Supabase X/Twitter OAuth."""
    site_url = _get_site_url(request)
    redirect_url = (
        f"{SUPABASE_URL}/auth/v1/authorize"
        f"?provider=twitter"
        f"&redirect_to={site_url}/api/auth/callback"
    )
    return RedirectResponse(redirect_url)


@app.get("/api/auth/callback")
async def auth_callback(request: Request):
    """OAuth callback — serves a small HTML page that reads the URL fragment
    (access_token, refresh_token) in JS and sets cookies, then redirects
    to /dashboard. Supabase redirects here with tokens in the hash fragment.
    """
    callback_html = """<!DOCTYPE html>
<html><head><title>Signing in...</title></head>
<body>
<p style="font-family:Inter,sans-serif;text-align:center;margin-top:40vh;color:#666;">Signing you in...</p>
<script>
(function() {
    // Supabase puts tokens in the URL hash fragment
    var hash = window.location.hash.substring(1);
    if (!hash) {
        // Check query params as fallback
        hash = window.location.search.substring(1);
    }
    var params = new URLSearchParams(hash);
    var accessToken = params.get('access_token');
    var refreshToken = params.get('refresh_token');

    if (accessToken) {
        // POST tokens to server to set httpOnly cookies
        fetch('/api/auth/set-session', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                access_token: accessToken,
                refresh_token: refreshToken || ''
            }),
            credentials: 'same-origin'
        }).then(function() {
            window.location.replace('/dashboard');
        }).catch(function() {
            window.location.replace('/dashboard');
        });
    } else {
        window.location.replace('/login?error=oauth_failed');
    }
})();
</script>
</body></html>"""
    return HTMLResponse(callback_html)


@app.post("/api/auth/set-session")
async def api_set_session(request: Request, body: dict = Body(...)):
    """Called by the OAuth callback JS to set httpOnly cookies from tokens."""
    access_token = body.get("access_token", "")
    refresh_token = body.get("refresh_token", "")
    if not access_token:
        return JSONResponse({"error": "No access token"}, status_code=400)

    # Validate the token with Supabase and create/sync local profile
    from web.supabase_client import supabase as sb
    try:
        user_response = sb.auth.get_user(access_token)
        sb_user = user_response.user
        if not sb_user:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        # Ensure local profile exists
        from web.auth import _get_or_create_profile
        email = sb_user.email or ""
        user_metadata = sb_user.user_metadata or {}
        username = user_metadata.get("username") or user_metadata.get("full_name", "").replace(" ", "").lower()[:20] or email.split("@")[0]
        provider = (sb_user.app_metadata or {}).get("provider", "email")
        _get_or_create_profile(sb_user.id, email, username, auth_provider=provider)
    except Exception:
        pass  # Still set cookies — get_current_user will validate later

    response = JSONResponse({"ok": True})
    _set_auth_cookies(response, access_token, refresh_token)
    return response


@app.post("/api/auth/logout")
async def api_logout():
    from web.supabase_client import supabase as sb
    try:
        sb.auth.sign_out()
    except Exception:
        pass
    response = JSONResponse({"ok": True})
    _delete_auth_cookies(response)
    return response


@app.get("/api/auth/logout")
async def api_logout_get():
    from web.supabase_client import supabase as sb
    try:
        sb.auth.sign_out()
    except Exception:
        pass
    response = RedirectResponse("/login")
    _delete_auth_cookies(response)
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


# ─── Stripe Checkout API ──────────────────────────────────────────────────────

@app.post("/api/checkout")
async def create_checkout(request: Request, body: dict = Body(...)):
    """Create a Stripe Checkout session for subscription."""
    user = require_user(request)
    tier = body.get("tier", "pro")

    import stripe
    stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")

    if not stripe.api_key:
        raise HTTPException(400, "Stripe not configured")

    # Price mapping — create these in Stripe Dashboard
    prices = {
        "starter": os.getenv("STRIPE_PRICE_STARTER", ""),
        "pro": os.getenv("STRIPE_PRICE_PRO", ""),
        "elite": os.getenv("STRIPE_PRICE_ELITE", ""),
    }

    price_id = prices.get(tier)
    if not price_id:
        raise HTTPException(400, f"Invalid tier: {tier}")

    base_url = str(request.base_url).rstrip("/")

    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=f"{base_url}/dashboard?subscribed=1",
            cancel_url=f"{base_url}/pricing",
            customer_email=user.email,
            metadata={"user_id": str(user.id), "tier": tier},
        )
        return {"url": session.url}
    except Exception as e:
        raise HTTPException(400, str(e))


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

@app.get("/connections", response_class=HTMLResponse)
async def connections_page(request: Request):
    user = require_user(request)
    return templates.TemplateResponse("connections.html", {"request": request, "user": user})




@app.get("/sources", response_class=HTMLResponse)
async def sources_page(request: Request):
    sources_path = BASE_DIR / "templates" / "sources.html"
    with open(sources_path, "r") as f:
        return HTMLResponse(f.read())


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


@app.get("/api/performance")
async def api_performance(request: Request):
    """Get bot performance summary with learning insights."""
    try:
        from core.trade_learner import trade_learner
        summary = trade_learner.get_performance_summary(30)
        return summary
    except Exception as e:
        return {"error": str(e), "message": "No trade data available yet"}



# ─── Broker API Routes ────────────────────────────────────────────────────────

@app.get("/api/brokers")
async def api_list_brokers():
    """Return all available broker integrations with metadata."""
    from core.brokers.factory import BrokerFactory
    return BrokerFactory.list_brokers()


@app.get("/api/connections")
async def api_list_connections(request: Request):
    """List the current user's connected broker accounts."""
    user = require_user(request)
    db = SessionLocal()
    try:
        from web.database import BrokerConnection
        conns = (
            db.query(BrokerConnection)
            .filter(BrokerConnection.user_id == user.id, BrokerConnection.is_active == True)
            .all()
        )
        return [
            {
                "id": c.id,
                "broker_name": c.broker_name,
                "display_name": c.display_name or c.broker_name,
                "account_id": c.account_id,
                "is_paper": c.is_paper,
                "created_at": c.created_at.isoformat() if c.created_at else None,
                "last_connected": c.last_connected.isoformat() if c.last_connected else None,
            }
            for c in conns
        ]
    finally:
        db.close()


@app.post("/api/connections")
async def api_add_connection(request: Request, body: dict = Body(...)):
    """Add a new broker connection for the current user.

    Expected body keys: broker_name, display_name, api_key, api_secret,
    account_id, access_token, refresh_token, extra (JSON string), is_paper
    """
    user = require_user(request)
    broker_name = body.get("broker_name", "").strip()
    if not broker_name:
        return JSONResponse({"error": "broker_name is required"}, status_code=400)

    from core.brokers.factory import BrokerFactory
    known = {b["name"] for b in BrokerFactory.list_brokers()}
    if broker_name not in known:
        return JSONResponse({"error": f"Unknown broker: {broker_name}"}, status_code=400)

    db = SessionLocal()
    try:
        from web.database import BrokerConnection
        from datetime import datetime as _dt
        conn = BrokerConnection(
            user_id=user.id,
            broker_name=broker_name,
            display_name=body.get("display_name"),
            api_key=body.get("api_key"),
            api_secret=body.get("api_secret"),
            account_id=body.get("account_id"),
            access_token=body.get("access_token"),
            refresh_token=body.get("refresh_token"),
            extra=body.get("extra"),
            is_paper=body.get("is_paper", False),
            is_active=True,
            created_at=_dt.utcnow(),
        )
        db.add(conn)
        db.commit()
        db.refresh(conn)
        return {"id": conn.id, "broker_name": conn.broker_name, "status": "connected"}
    except Exception as e:
        db.rollback()
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        db.close()


@app.delete("/api/connections/{connection_id}")
async def api_remove_connection(request: Request, connection_id: int):
    """Soft-delete a broker connection."""
    user = require_user(request)
    db = SessionLocal()
    try:
        from web.database import BrokerConnection
        conn = db.query(BrokerConnection).filter(
            BrokerConnection.id == connection_id,
            BrokerConnection.user_id == user.id,
        ).first()
        if not conn:
            return JSONResponse({"error": "Connection not found"}, status_code=404)
        conn.is_active = False
        db.commit()
        return {"status": "removed"}
    except Exception as e:
        db.rollback()
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        db.close()


@app.post("/api/connections/{connection_id}/test")
async def api_test_connection(request: Request, connection_id: int):
    """Test a broker connection by pinging the account endpoint."""
    user = require_user(request)
    db = SessionLocal()
    try:
        from web.database import BrokerConnection
        from core.brokers.factory import BrokerFactory
        from datetime import datetime as _dt
        import json as _json

        conn = db.query(BrokerConnection).filter(
            BrokerConnection.id == connection_id,
            BrokerConnection.user_id == user.id,
            BrokerConnection.is_active == True,
        ).first()
        if not conn:
            return JSONResponse({"error": "Connection not found"}, status_code=404)

        # Build credentials dict from stored fields
        creds: dict = {}
        if conn.api_key:
            creds["api_key"] = conn.api_key
        if conn.api_secret:
            creds["api_secret"] = conn.api_secret
        if conn.account_id:
            creds["account_id"] = conn.account_id
        if conn.access_token:
            creds["access_token"] = conn.access_token
        if conn.extra:
            try:
                extra = _json.loads(conn.extra)
                creds.update(extra)
            except Exception:
                pass

        if conn.broker_name == "paper":
            creds.setdefault("starting_cash", 100_000.0)

        try:
            broker = BrokerFactory.create(conn.broker_name, creds)
            acct = broker.get_account()
            conn.last_connected = _dt.utcnow()
            db.commit()
            return {"status": "ok", "account": acct}
        except Exception as e:
            return JSONResponse({"status": "error", "message": str(e)}, status_code=400)
    finally:
        db.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("web.server:app", host="0.0.0.0", port=3000, reload=True)
