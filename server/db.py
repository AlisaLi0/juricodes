"""SQLite store for leagle accounts and saved research sessions.

Two tables, one small file (LEAGLE_DB_PATH, default ./leagle.db):

* users             — one row per signed-in account (OAuth identity). Carries
                      forward-looking `plan` / `credits` columns so billing can
                      be layered on later without a migration, but nothing here
                      charges money.
* research_sessions — a user's saved research threads (the durable version of
                      the browser-only history), each a JSON snapshot of the
                      conversation + retrieved authorities.

Access is synchronous sqlite3 wrapped in asyncio.to_thread by the callers; the
data is tiny (accounts + short JSON threads) so this is more than enough and
keeps the dependency footprint at zero beyond the standard library.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass

DB_PATH = os.getenv("LEAGLE_DB_PATH", os.path.join(os.path.dirname(__file__), "..", "leagle.db"))

# Plan -> monthly research-question allowance. "research" = one /api/chat ask.
# Free is generous enough to prove value but nudges heavy users to upgrade;
# paid tiers map to the Freemius plans (pro/max).
PLANS: dict[str, dict] = {
    "free": {"label": "Free",     "monthly_questions": 10,    "price": 0},
    "pro":  {"label": "Pro",      "monthly_questions": 300,   "price": 9.98},
    "max":  {"label": "Max",      "monthly_questions": 100000, "price": 29.98},  # "soft" unlimited
}
DEFAULT_PLAN = "free"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    provider          TEXT NOT NULL,
    provider_user_id  TEXT NOT NULL,
    email             TEXT,
    name              TEXT,
    avatar_url        TEXT,
    plan              TEXT NOT NULL DEFAULT 'free',
    credits           INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL,
    last_login        TEXT,
    UNIQUE(provider, provider_user_id)
);
CREATE TABLE IF NOT EXISTS research_sessions (
    id          TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL,
    title       TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    payload     TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON research_sessions(user_id, updated_at DESC);
CREATE TABLE IF NOT EXISTS usage_monthly (
    user_id     INTEGER NOT NULL,
    month       TEXT NOT NULL,            -- 'YYYY-MM' (UTC)
    questions   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, month)
);
CREATE TABLE IF NOT EXISTS subscriptions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL,
    plan          TEXT NOT NULL,
    provider      TEXT,                   -- 'freemius'
    provider_ref  TEXT,
    status        TEXT NOT NULL DEFAULT 'pending',
    period_end    INTEGER,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sub_user ON subscriptions(user_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_sub_ref ON subscriptions(provider, provider_ref);
"""


@dataclass(slots=True)
class User:
    id: int
    provider: str
    provider_user_id: str
    email: str
    name: str
    avatar_url: str
    plan: str
    credits: int

    def to_public(self) -> dict:
        """Fields safe to expose to the browser (no internal flags)."""
        return {
            "id": self.id,
            "email": self.email,
            "name": self.name,
            "avatar_url": self.avatar_url,
            "plan": self.plan,
            "credits": self.credits,
        }


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    conn = _connect()
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def _row_to_user(r: sqlite3.Row) -> User:
    return User(
        id=r["id"], provider=r["provider"], provider_user_id=r["provider_user_id"],
        email=r["email"] or "", name=r["name"] or "", avatar_url=r["avatar_url"] or "",
        plan=r["plan"], credits=r["credits"],
    )


# ── Users ──────────────────────────────────────────────────────────────────

def upsert_user(provider: str, provider_user_id: str, *, email: str = "",
                name: str = "", avatar_url: str = "") -> User:
    """Create or update the account for an OAuth identity, returning the User.

    Keyed by (provider, provider_user_id) so the same person signing in again is
    matched to their existing account (and profile fields refreshed).
    """
    conn = _connect()
    try:
        now = _now()
        conn.execute(
            """INSERT INTO users (provider, provider_user_id, email, name, avatar_url,
                                  created_at, last_login)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(provider, provider_user_id) DO UPDATE SET
                   email=excluded.email, name=excluded.name,
                   avatar_url=excluded.avatar_url, last_login=excluded.last_login""",
            (provider, str(provider_user_id), email, name, avatar_url, now, now),
        )
        conn.commit()
        r = conn.execute(
            "SELECT * FROM users WHERE provider=? AND provider_user_id=?",
            (provider, str(provider_user_id)),
        ).fetchone()
        return _row_to_user(r)
    finally:
        conn.close()


def get_user(user_id: int) -> User | None:
    conn = _connect()
    try:
        r = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return _row_to_user(r) if r else None
    finally:
        conn.close()


# ── Research sessions ──────────────────────────────────────────────────────

def list_sessions(user_id: int, *, limit: int = 50) -> list[dict]:
    """Session metadata (no payload) for the sidebar, newest first."""
    conn = _connect()
    try:
        rows = conn.execute(
            """SELECT id, title, created_at, updated_at FROM research_sessions
               WHERE user_id=? ORDER BY updated_at DESC LIMIT ?""",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_session(user_id: int, session_id: str) -> dict | None:
    conn = _connect()
    try:
        r = conn.execute(
            "SELECT * FROM research_sessions WHERE id=? AND user_id=?",
            (session_id, user_id),
        ).fetchone()
        if not r:
            return None
        d = dict(r)
        try:
            d["payload"] = json.loads(d["payload"])
        except (ValueError, TypeError):
            d["payload"] = []
        return d
    finally:
        conn.close()


def save_session(user_id: int, *, session_id: str | None, title: str,
                 payload: list | dict) -> str:
    """Insert or update a saved research thread; returns its id."""
    conn = _connect()
    try:
        now = _now()
        sid = session_id or uuid.uuid4().hex
        body = json.dumps(payload, ensure_ascii=False)
        title = (title or "Untitled research")[:200]
        exists = conn.execute(
            "SELECT 1 FROM research_sessions WHERE id=? AND user_id=?",
            (sid, user_id),
        ).fetchone()
        if exists:
            conn.execute(
                "UPDATE research_sessions SET title=?, payload=?, updated_at=? "
                "WHERE id=? AND user_id=?",
                (title, body, now, sid, user_id),
            )
        else:
            conn.execute(
                """INSERT INTO research_sessions (id, user_id, title, created_at,
                                                  updated_at, payload)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (sid, user_id, title, now, now, body),
            )
        conn.commit()
        return sid
    finally:
        conn.close()


def delete_session(user_id: int, session_id: str) -> bool:
    conn = _connect()
    try:
        cur = conn.execute(
            "DELETE FROM research_sessions WHERE id=? AND user_id=?",
            (session_id, user_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ── Billing: plan, usage quota, subscriptions ───────────────────────────────

def get_user_by_email(email: str) -> User | None:
    """Look up an account by email (used by the Freemius webhook)."""
    if not email:
        return None
    conn = _connect()
    try:
        r = conn.execute(
            "SELECT * FROM users WHERE lower(email)=lower(?) ORDER BY id LIMIT 1",
            (email.strip(),),
        ).fetchone()
        return _row_to_user(r) if r else None
    finally:
        conn.close()


def set_plan(user_id: int, plan: str) -> None:
    if plan not in PLANS:
        plan = DEFAULT_PLAN
    conn = _connect()
    try:
        conn.execute("UPDATE users SET plan=? WHERE id=?", (plan, user_id))
        conn.commit()
    finally:
        conn.close()


def _month() -> str:
    return time.strftime("%Y-%m", time.gmtime())


def usage_this_month(user_id: int) -> int:
    conn = _connect()
    try:
        r = conn.execute(
            "SELECT questions FROM usage_monthly WHERE user_id=? AND month=?",
            (user_id, _month()),
        ).fetchone()
        return r["questions"] if r else 0
    finally:
        conn.close()


def try_consume_question(user_id: int, limit: int) -> bool:
    """Atomically count one research question if under the monthly limit.

    Returns False (and counts nothing) when the user is already at their limit.
    """
    month = _month()
    conn = _connect()
    try:
        r = conn.execute(
            "SELECT questions FROM usage_monthly WHERE user_id=? AND month=?",
            (user_id, month),
        ).fetchone()
        used = r["questions"] if r else 0
        if used >= limit:
            return False
        conn.execute(
            "INSERT INTO usage_monthly (user_id, month, questions) VALUES (?, ?, 1) "
            "ON CONFLICT(user_id, month) DO UPDATE SET questions = questions + 1",
            (user_id, month),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def refund_question(user_id: int) -> None:
    """Give back one counted question (e.g. when the request errored out)."""
    conn = _connect()
    try:
        conn.execute(
            "UPDATE usage_monthly SET questions = MAX(0, questions - 1) "
            "WHERE user_id=? AND month=?",
            (user_id, _month()),
        )
        conn.commit()
    finally:
        conn.close()


def upsert_subscription(user_id: int, plan: str, provider: str,
                        provider_ref: str, status: str,
                        period_end: int | None = None) -> None:
    """Record/refresh a subscription keyed on (provider, provider_ref)."""
    now = _now()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT id FROM subscriptions WHERE provider=? AND provider_ref=?",
            (provider, provider_ref),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE subscriptions SET user_id=?, plan=?, status=?, "
                "period_end=?, updated_at=? WHERE id=?",
                (user_id, plan, status, period_end, now, row["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO subscriptions (user_id, plan, provider, provider_ref, "
                "status, period_end, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (user_id, plan, provider, provider_ref, status, period_end, now, now),
            )
        conn.commit()
    finally:
        conn.close()

