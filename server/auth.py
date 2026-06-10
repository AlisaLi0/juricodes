"""Authentication for leagle — OAuth sign-in + signed-cookie sessions.

leagle accounts exist so a user's research can be saved to them (and, later,
so usage can be metered/billed). Sign-in is delegated to an OAuth provider
(GitHub or Google) — we never store passwords. After the provider confirms the
identity we mint a signed session cookie (HMAC-SHA256 over a tiny JSON payload),
so there is no server-side session table and no extra dependency beyond the
standard library + httpx (already used for retrieval).

Flow:
  GET  /api/auth/{provider}/start     -> 302 to the provider's consent page
  GET  /api/auth/{provider}/callback  -> exchange code, upsert user, set cookie
  GET  /api/auth/me                   -> current user (from cookie) or 401
  POST /api/auth/logout               -> clear cookie

Configuration (env):
  LEAGLE_SECRET_KEY        signing key for the session cookie (REQUIRED in prod)
  LEAGLE_PUBLIC_BASE       public base URL, e.g. https://juricodex.online
  GITHUB_CLIENT_ID/SECRET  GitHub OAuth app credentials
  GOOGLE_CLIENT_ID/SECRET  Google OAuth app credentials
Only providers whose credentials are present are offered.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

import httpx

from . import db

COOKIE_NAME = "leagle_session"
COOKIE_MAX_AGE = 30 * 24 * 3600  # 30 days
# Scope the session cookie to the app's path so it is not sent to other apps on
# the same domain (leagle is reverse-proxied under /leagle on a shared host).
COOKIE_PATH = os.getenv("LEAGLE_COOKIE_PATH", "/")
_STATE_MAX_AGE = 600  # OAuth state/login validity: 10 minutes

# A stable per-process secret if none is configured. Fine for local dev; in
# production LEAGLE_SECRET_KEY must be set or restarts would invalidate cookies.
_SECRET = (os.getenv("LEAGLE_SECRET_KEY") or "").encode() or secrets.token_bytes(32)
PUBLIC_BASE = os.getenv("LEAGLE_PUBLIC_BASE", "").rstrip("/")


# ── OAuth provider registry ─────────────────────────────────────────────────

PROVIDERS = {
    "github": {
        "authorize": "https://github.com/login/oauth/authorize",
        "token": "https://github.com/login/oauth/access_token",
        "userinfo": "https://api.github.com/user",
        "emails": "https://api.github.com/user/emails",
        "scope": "read:user user:email",
        "client_id": os.getenv("GITHUB_CLIENT_ID", ""),
        "client_secret": os.getenv("GITHUB_CLIENT_SECRET", ""),
    },
    "google": {
        "authorize": "https://accounts.google.com/o/oauth2/v2/auth",
        "token": "https://oauth2.googleapis.com/token",
        "userinfo": "https://openidconnect.googleapis.com/v1/userinfo",
        "scope": "openid email profile",
        "client_id": os.getenv("GOOGLE_CLIENT_ID", ""),
        "client_secret": os.getenv("GOOGLE_CLIENT_SECRET", ""),
    },
}


def configured_providers() -> list[str]:
    """Providers that have both a client id and secret set."""
    return [p for p, c in PROVIDERS.items() if c["client_id"] and c["client_secret"]]


def redirect_uri(provider: str) -> str:
    base = PUBLIC_BASE or "http://127.0.0.1:8600"
    return f"{base}/api/auth/{provider}/callback"


# ── Signed tokens (session cookie + OAuth state), dependency-free ───────────

def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(payload: dict) -> str:
    body = _b64e(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    sig = hmac.new(_SECRET, body.encode(), hashlib.sha256).digest()
    return f"{body}.{_b64e(sig)}"


def _unsign(token: str) -> dict | None:
    try:
        body, sig = token.split(".", 1)
    except (ValueError, AttributeError):
        return None
    expect = hmac.new(_SECRET, body.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(_b64d(sig), expect):
        return None
    try:
        return json.loads(_b64d(body))
    except (ValueError, TypeError):
        return None


def make_session_cookie(user_id: int) -> str:
    return _sign({"uid": int(user_id), "exp": int(time.time()) + COOKIE_MAX_AGE})


def make_state(provider: str, next_url: str = "") -> str:
    return _sign({"p": provider, "n": next_url[:300],
                  "exp": int(time.time()) + _STATE_MAX_AGE,
                  "nonce": secrets.token_urlsafe(8)})


def read_state(token: str, provider: str) -> dict | None:
    data = _unsign(token)
    if not data or data.get("p") != provider:
        return None
    if int(data.get("exp", 0)) < time.time():
        return None
    return data


def user_id_from_cookie(token: str | None) -> int | None:
    if not token:
        return None
    data = _unsign(token)
    if not data or int(data.get("exp", 0)) < time.time():
        return None
    try:
        return int(data["uid"])
    except (KeyError, ValueError, TypeError):
        return None


# ── OAuth handshake ─────────────────────────────────────────────────────────

def authorize_url(provider: str, state: str) -> str:
    cfg = PROVIDERS[provider]
    from urllib.parse import urlencode
    params = {
        "client_id": cfg["client_id"],
        "redirect_uri": redirect_uri(provider),
        "scope": cfg["scope"],
        "state": state,
        "response_type": "code",
    }
    if provider == "google":
        params["access_type"] = "online"
        params["prompt"] = "select_account"
    return f"{cfg['authorize']}?{urlencode(params)}"


async def exchange_code(provider: str, code: str) -> db.User | None:
    """Exchange an OAuth code for the provider's profile and upsert the account."""
    cfg = PROVIDERS[provider]
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        tok_resp = await client.post(
            cfg["token"],
            data={
                "client_id": cfg["client_id"],
                "client_secret": cfg["client_secret"],
                "code": code,
                "redirect_uri": redirect_uri(provider),
                "grant_type": "authorization_code",
            },
            headers={"Accept": "application/json"},
        )
        tok_resp.raise_for_status()
        tok = tok_resp.json()
        access = tok.get("access_token")
        if not access:
            return None
        auth_headers = {"Authorization": f"Bearer {access}",
                        "Accept": "application/json",
                        "User-Agent": "leagle-chat"}
        info = (await client.get(cfg["userinfo"], headers=auth_headers)).json()

        if provider == "github":
            puid = str(info.get("id") or "")
            name = info.get("name") or info.get("login") or ""
            avatar = info.get("avatar_url") or ""
            email = info.get("email") or ""
            if not email:  # GitHub hides email by default; fetch verified primary
                try:
                    emails = (await client.get(cfg["emails"], headers=auth_headers)).json()
                    primary = next((e for e in emails if e.get("primary") and e.get("verified")), None)
                    email = (primary or (emails[0] if emails else {})).get("email", "")
                except (httpx.HTTPError, ValueError, IndexError):
                    email = ""
        else:  # google
            puid = str(info.get("sub") or "")
            name = info.get("name") or ""
            avatar = info.get("picture") or ""
            email = info.get("email") or ""

        if not puid:
            return None
        return db.upsert_user(provider, puid, email=email, name=name, avatar_url=avatar)
