"""Stripe integration — subscriptions, deposits, webhooks"""
from __future__ import annotations
import os, json, hmac, hashlib
from datetime import datetime, timedelta
import requests

STRIPE_SECRET_KEY    = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
STRIPE_BASE          = "https://api.stripe.com/v1"


def _stripe(method: str, endpoint: str, data: dict | None = None) -> dict:
    """Make a Stripe API call."""
    if not STRIPE_SECRET_KEY:
        return {"error": "Stripe not configured"}
    headers = {"Authorization": f"Bearer {STRIPE_SECRET_KEY}"}
    url = f"{STRIPE_BASE}{endpoint}"
    if method == "GET":
        resp = requests.get(url, headers=headers, params=data)
    else:
        resp = requests.post(url, headers=headers, data=data)
    return resp.json()


def create_customer(email: str, name: str) -> str | None:
    """Create a Stripe customer, return customer_id."""
    result = _stripe("POST", "/customers", {"email": email, "name": name})
    return result.get("id")


def create_checkout_session(customer_id: str, price_id: str, success_url: str, cancel_url: str) -> str | None:
    """Create a Stripe Checkout Session for subscription, return URL."""
    data = {
        "customer": customer_id,
        "mode": "subscription",
        "line_items[0][price]": price_id,
        "line_items[0][quantity]": "1",
        "success_url": success_url,
        "cancel_url": cancel_url,
        "subscription_data[trial_period_days]": "14",
        "allow_promotion_codes": "true",
    }
    result = _stripe("POST", "/checkout/sessions", data)
    return result.get("url")


def create_deposit_intent(amount_cents: int, customer_id: str) -> dict:
    """Create a PaymentIntent for a one-time deposit."""
    data = {
        "amount": str(amount_cents),
        "currency": "usd",
        "customer": customer_id,
        "automatic_payment_methods[enabled]": "true",
        "metadata[type]": "deposit",
    }
    return _stripe("POST", "/payment_intents", data)


def get_subscription(sub_id: str) -> dict:
    return _stripe("GET", f"/subscriptions/{sub_id}")


def cancel_subscription(sub_id: str) -> dict:
    return _stripe("POST", f"/subscriptions/{sub_id}/cancel")


def create_billing_portal(customer_id: str, return_url: str) -> str | None:
    """Return billing portal URL."""
    result = _stripe("POST", "/billing_portal/sessions", {
        "customer": customer_id,
        "return_url": return_url,
    })
    return result.get("url")


def verify_webhook(payload: bytes, sig_header: str) -> dict | None:
    """Verify Stripe webhook signature and return event."""
    if not STRIPE_WEBHOOK_SECRET:
        return None
    try:
        parts = {k: v for item in sig_header.split(",") for k, v in [item.split("=", 1)]}
        ts = int(parts.get("t", 0))
        sig = parts.get("v1", "")
        signed = f"{ts}.{payload.decode()}"
        expected = hmac.new(STRIPE_WEBHOOK_SECRET.encode(), signed.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            return None
        return json.loads(payload)
    except Exception:
        return None
