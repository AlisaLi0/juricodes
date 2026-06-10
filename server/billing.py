"""Freemius billing for JuriCodex — checkout config + webhook sync.

Freemius hosts the checkout, subscription management and tax handling; it then
notifies us via a signed webhook whenever a subscription/license changes, and we
flip the user's `plan` in our own DB accordingly. Nothing here charges money or
stores card data — Freemius owns that.

Config (env, only set on the server):
  FREEMIUS_PRODUCT_ID     numeric product id (public)
  FREEMIUS_PUBLIC_KEY     public key for the checkout widget (public)
  FREEMIUS_SECRET_KEY     product secret key — verifies webhook signatures (SECRET)
  FREEMIUS_PLANS          comma list of  our_plan:plan_id:pricing_id  triples,
                          e.g. "pro:52037:68208,max:52038:68209"
Only when PRODUCT_ID + SECRET are present is billing considered enabled.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time

from . import db

PRODUCT_ID = os.getenv("FREEMIUS_PRODUCT_ID", "").strip()
PUBLIC_KEY = os.getenv("FREEMIUS_PUBLIC_KEY", "").strip()
SECRET_KEY = os.getenv("FREEMIUS_SECRET_KEY", "").strip()
PORTAL_URL = os.getenv("FREEMIUS_PORTAL_URL", "").strip()
SUPPORT_EMAIL = os.getenv("SUPPORT_EMAIL", "support@juricodex.online").strip()
SANDBOX = os.getenv("FREEMIUS_SANDBOX", "false").strip().lower() in {"1", "true", "yes", "on"}

# our_plan -> Freemius plan_id (used to build a checkout link)
PLAN_TO_FREEMIUS: dict[str, str] = {}
# our_plan -> Freemius pricing_id (used to pick the exact paid price/cadence)
PRICING_TO_FREEMIUS: dict[str, str] = {}
# Freemius plan_id AND pricing_id -> our_plan (used to read a webhook back)
FREEMIUS_TO_PLAN: dict[str, str] = {}
for _triple in os.getenv("FREEMIUS_PLANS", "").split(","):
    _p = [x.strip() for x in _triple.split(":") if x.strip()]
    if len(_p) >= 2:
        _our, _plan_id = _p[0], _p[1]
        PLAN_TO_FREEMIUS[_our] = _plan_id
        FREEMIUS_TO_PLAN[_plan_id] = _our
        if len(_p) >= 3:                 # pricing_id also maps back to our plan
            PRICING_TO_FREEMIUS[_our] = _p[2]
            FREEMIUS_TO_PLAN[_p[2]] = _our


def enabled() -> bool:
    return bool(PRODUCT_ID and SECRET_KEY)


def public_config() -> dict | None:
    """Checkout params safe to expose to the browser, or None if disabled."""
    if not enabled():
        return None
    return {
        "product_id": PRODUCT_ID,
        "public_key": PUBLIC_KEY,
        "plans": PLAN_TO_FREEMIUS,
        "pricing": PRICING_TO_FREEMIUS,
        "sandbox": SANDBOX,
        "portal_url": PORTAL_URL,
        "support_email": SUPPORT_EMAIL,
    }


def verify_signature(raw_body: bytes, header_sig: str) -> bool:
    """HMAC-SHA256 of the raw request body with the product secret key (hex)."""
    if not header_sig or not SECRET_KEY:
        return False
    sig = header_sig.strip()
    for pref in ("Bearer ", "FSA ", "sha256="):
        if sig.startswith(pref):
            sig = sig[len(pref):]
    expected = hmac.new(SECRET_KEY.encode(), raw_body, hashlib.sha256).hexdigest()
    try:
        return hmac.compare_digest(sig.lower(), expected.lower())
    except Exception:
        return False


def _plan_from_obj(obj: dict) -> str | None:
    for key in ("pricing_id", "plan_id"):
        val = obj.get(key)
        if val is not None and str(val) in FREEMIUS_TO_PLAN:
            return FREEMIUS_TO_PLAN[str(val)]
    return None


# Event types that should grant or revoke access. Freemius may add more, so any
# type not listed here is acknowledged and ignored (no accidental plan change).
ACTIVATE = frozenset({
    "subscription.created",
    "payment.created",             # initial charge AND every successful renewal
    "license.created",
    "license.activated",           # canonical "upgrade succeeded" event
    "license.updated",
    "license.extended",
    "plan.lifetime.purchase",
})
DEACTIVATE = frozenset({
    "subscription.cancelled",
    "subscription.renewal.failed.last",
    "payment.refund",
    "payment.dispute.created",     # chargeback -> revoke
    "license.cancelled",
    "license.deactivated",
    "license.expired",
    "license.deleted",
})


def _parse_period_end(obj: dict) -> int | None:
    raw = obj.get("next_payment") or obj.get("expiration")
    if not raw:
        return None
    try:
        return int(time.mktime(time.strptime(str(raw)[:19], "%Y-%m-%d %H:%M:%S")))
    except Exception:
        return None


def _event_id(evt: dict) -> str:
    raw = evt.get("id") or evt.get("event_id") or evt.get("uuid")
    if raw:
        return str(raw)
    body = json.dumps(evt or {}, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(body.encode()).hexdigest()


def handle_event(evt: dict) -> dict:
    """Apply a (already signature-verified) Freemius event to our DB.

    Returns a small dict describing the outcome (for logging / the HTTP body).
    Always succeeds with 2xx semantics so Freemius stops retrying: unknown users
    or event types are acknowledged and ignored.
    """
    etype = (evt.get("type") or "").lower()
    event_id = _event_id(evt)
    objects = evt.get("objects") or {}
    user_obj = objects.get("user") or {}
    email = (user_obj.get("email") or evt.get("user_email") or "").strip().lower()

    if not db.add_billing_event("freemius", event_id, etype, email, evt):
        return {"ok": True, "action": "duplicate", "type": etype, "event_id": event_id}

    # Housekeeping: lapse any day passes that have run out (cheap, best-effort).
    try:
        db.expire_day_passes()
    except Exception:
        pass

    sub_obj = objects.get("subscription") or objects.get("license") or {}
    plan = _plan_from_obj(sub_obj) or _plan_from_obj(objects.get("plan") or {})
    ref = str(sub_obj.get("id") or (objects.get("license") or {}).get("id")
              or evt.get("id") or etype)
    period_end = _parse_period_end(sub_obj)

    user = db.get_user_by_email(email) if email else None
    if not user:
        # No account with this payer email yet. Don't drop a paid upgrade on the
        # floor: park it so it's applied the moment an account with this email
        # signs in. Revocations for unknown users are no-ops.
        if etype in ACTIVATE and plan and email:
            db.add_pending_billing(email, plan, "freemius", ref, period_end, etype)
            db.finish_billing_event("freemius", event_id, user_id=None, plan=plan,
                                    action="pending_account")
            return {"ok": True, "note": "parked pending account", "plan": plan,
                    "email": email, "type": etype, "event_id": event_id}
        db.finish_billing_event("freemius", event_id, user_id=None, plan=plan,
                                action="user_not_found")
        return {"ok": True, "note": "user not found", "type": etype}

    if etype in DEACTIVATE:
        db.set_plan(user.id, "free")
        if plan:
            db.upsert_subscription(user.id, plan, "freemius", ref, "cancelled", period_end)
        db.finish_billing_event("freemius", event_id, user_id=user.id, plan=plan,
                                action="downgrade")
        return {"ok": True, "action": "downgrade", "type": etype, "user": user.id,
                "event_id": event_id}

    if etype in ACTIVATE and plan:
        if plan == "day_pass":
            # One-off purchase: grant Max-level access for PASS_DAYS without
            # touching the user's stored plan (it lapses automatically when the
            # subscription's period_end passes — see db.effective_plan).
            pass_end = int(time.time()) + db.PASS_DAYS * 86400
            db.upsert_subscription(user.id, "day_pass", "freemius", ref, "active", pass_end)
            db.finish_billing_event("freemius", event_id, user_id=user.id, plan=plan,
                                    action="day_pass")
            return {"ok": True, "action": "day_pass", "days": db.PASS_DAYS,
                    "type": etype, "user": user.id, "event_id": event_id}
        db.set_plan(user.id, plan)
        db.upsert_subscription(user.id, plan, "freemius", ref, "active", period_end)
        db.finish_billing_event("freemius", event_id, user_id=user.id, plan=plan,
                                action="activate")
        return {"ok": True, "action": "activate", "plan": plan, "type": etype,
                "user": user.id, "event_id": event_id}

    db.finish_billing_event("freemius", event_id, user_id=user.id, plan=plan,
                            action="ignored")
    return {"ok": True, "action": "ignored", "type": etype, "plan": plan}


def reconcile_pending(user: "db.User") -> int:
    """Apply any purchases that were parked before this account existed/was
    matchable, now that the user has signed in with a matching email. Called on
    login. Returns the number of purchases applied."""
    if not user or not user.email:
        return 0
    try:
        pending = db.take_pending_billing(user.email)
    except Exception:
        return 0
    applied = 0
    now = int(time.time())
    for p in pending:
        plan = p.get("plan")
        if plan not in db.PLANS:
            continue
        ref = str(p.get("provider_ref") or "")
        period_end = p.get("period_end")
        if plan == "day_pass":
            pass_end = period_end if (period_end and period_end > now) \
                else now + db.PASS_DAYS * 86400
            db.upsert_subscription(user.id, "day_pass", "freemius", ref, "active", pass_end)
        else:
            db.set_plan(user.id, plan)
            db.upsert_subscription(user.id, plan, "freemius", ref, "active", period_end)
        applied += 1
    return applied
