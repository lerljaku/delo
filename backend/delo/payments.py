"""Stripe webhooks, without the Stripe SDK (the Lambda only has the standard library + boto3).

Checkout uses Stripe Payment Links (configured in Terraform, opened by the frontend with
?client_reference_id=<Cognito sub>): a one-time payment buys Supporter, a subscription Diamond.
See docs/ARCHITECTURE.md §7.
"""
from __future__ import annotations

import hashlib
import hmac
import time

TOLERANCE_SECONDS = 300
ACTIVE_SUBSCRIPTION = ("active", "trialing", "past_due")  # past_due: Stripe is still retrying the card


class SignatureError(ValueError):
    pass


def sign(raw: str, secret: str, timestamp: int) -> str:
    """The Stripe-Signature header Stripe would send (used by tests and local experiments)."""
    digest = hmac.new(secret.encode(), f"{timestamp}.{raw}".encode(), hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def verify_signature(raw: str, header: str, secret: str, now: float | None = None) -> None:
    """Check a Stripe-Signature header ("t=<unix time>,v1=<hex hmac>[,v1=...]")."""
    parts = [p.split("=", 1) for p in (header or "").split(",") if "=" in p]
    timestamps = [v for k, v in parts if k == "t"]
    signatures = [v for k, v in parts if k == "v1"]
    if not timestamps or not signatures or not timestamps[0].isdigit():
        raise SignatureError("Missing or malformed Stripe-Signature header")
    timestamp = int(timestamps[0])
    expected = sign(raw, secret, timestamp).split("v1=", 1)[1]
    if not any(hmac.compare_digest(expected, s) for s in signatures):
        raise SignatureError("Invalid Stripe signature")
    if abs((now if now is not None else time.time()) - timestamp) > TOLERANCE_SECONDS:
        raise SignatureError("Stripe signature timestamp is too old")


def membership(account: dict) -> str:
    """Paid level of an account: an active Diamond subscription, else a Supporter purchase."""
    return "diamond" if account.get("diamond") else "supporter" if account.get("supporter") else ""
