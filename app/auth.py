"""Accounts, sessions and profile API.

Design choices (all stdlib, no extra dependencies):
* Passwords: scrypt (n=2^14, r=8, p=1) with a per-user random salt; compared in constant time.
  A dummy hash is verified when the email is unknown, so login timing does not reveal who has an account.
* Sessions: a random 256-bit token in an HttpOnly, SameSite=Lax cookie. Only its SHA-256 is stored, so a
  database leak cannot be replayed as cookies. Sessions are individually listable and revocable.
* CSRF: SameSite=Lax plus a required custom header (X-Requested-With) and an Origin check on every unsafe
  request (see middleware in app/main.py). A cross-site form cannot set that header.
* Abuse: sliding-window rate limits on sign-in, sign-up and audit starts.
* No email service is configured, so there is no email verification or emailed reset. Passwords can be
  reset by the operator with `python -m scripts.manage_users reset <email>`.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import re
import secrets
import shutil
import sqlite3
import threading
import time
from contextvars import ContextVar
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.config import settings

COOKIE = "pd_session"
SESSION_PERSISTENT = 30 * 24 * 3600
SESSION_SHORT = 12 * 3600
GUEST_TTL = 24 * 3600
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
COMMON = {
    "password", "password1", "password123", "123456789", "1234567890", "qwertyuiop", "iloveyou1", "letmein123",
    "admin12345", "welcome123", "abc123456", "11111111", "00000000", "passw0rd1", "qwerty12345", "changeme123",
}
USE_CASES = ["shopping", "travel", "tickets", "subscriptions", "software", "food"]
COUNTRIES = ["IN", "US", "GB", "CA", "DE", "FR", "JP", "AU", "SG", "AE", "OTHER"]
CURRENT_USER: ContextVar[Optional[int]] = ContextVar("current_user", default=None)

_db_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


# --------------------------------------------------------------------------------- database
def db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        path = settings.data_dir / "pardarshi.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
        _conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users(
              id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL, pw_hash TEXT,
              is_guest INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL, last_login INTEGER,
              country TEXT NOT NULL DEFAULT '', city TEXT NOT NULL DEFAULT '', use_cases TEXT NOT NULL DEFAULT '[]',
              complaint_name TEXT NOT NULL DEFAULT '', default_provider TEXT NOT NULL DEFAULT '',
              default_quick INTEGER NOT NULL DEFAULT 0, theme TEXT NOT NULL DEFAULT 'system',
              onboarded INTEGER NOT NULL DEFAULT 0, hue INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS sessions(
              id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              token_hash TEXT UNIQUE NOT NULL, created_at INTEGER NOT NULL, last_seen INTEGER NOT NULL,
              expires_at INTEGER NOT NULL, ua TEXT NOT NULL DEFAULT '', ip TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS audit_owner(
              audit_id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE);
            CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
            """
        )
        _conn.execute("DELETE FROM sessions WHERE expires_at < ?", (int(time.time()),))
        _purge_guests(_conn)
    return _conn


def _purge_guests(conn: sqlite3.Connection) -> None:
    old = int(time.time()) - 2 * GUEST_TTL
    rows = conn.execute("SELECT id FROM users WHERE is_guest=1 AND created_at < ?", (old,)).fetchall()
    for r in rows:
        _delete_user_data(conn, r["id"])


class _Result:
    """Rows are read while the lock is held, so the shared connection is never used concurrently."""

    def __init__(self, rows: list, lastrowid: int | None):
        self._rows, self.lastrowid = rows, lastrowid

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows

    def __iter__(self):
        return iter(self._rows)


def q(sql: str, args: tuple = ()) -> _Result:
    with _db_lock:
        cur = db().execute(sql, args)
        return _Result(cur.fetchall() if cur.description else [], cur.lastrowid)


# --------------------------------------------------------------------------------- passwords
def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    n, r, p = 2 ** 14, 8, 1
    h = hashlib.scrypt(pw.encode(), salt=salt, n=n, r=r, p=p, dklen=32)
    return f"scrypt${n}${r}${p}${base64.b64encode(salt).decode()}${base64.b64encode(h).decode()}"


def verify_password(pw: str, stored: str | None) -> bool:
    try:
        _, n, r, p, salt, h = (stored or DUMMY_HASH).split("$")
        calc = hashlib.scrypt(pw.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(calc, base64.b64decode(h)) and stored is not None
    except Exception:
        return False


DUMMY_HASH = hash_password("not-a-real-password-just-for-timing")


def password_problem(pw: str, email: str = "", name: str = "") -> str | None:
    if len(pw) < 10:
        return "Use at least 10 characters."
    if len(pw) > 200:
        return "That password is too long."
    low = pw.lower()
    if low in COMMON or len(set(low)) < 4:
        return "That password is too easy to guess."
    squash = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())  # "ana-rao" must match "anarao"
    local = squash(email.split("@")[0])
    if len(local) >= 4 and local in squash(pw):
        return "Do not include your email name in your password."
    if not (re.search(r"[A-Za-z]", pw) and re.search(r"[0-9\W_]", pw)):
        return "Mix letters with at least one number or symbol."
    return None


# --------------------------------------------------------------------------------- rate limiting
class Limiter:
    def __init__(self) -> None:
        self.hits: dict[str, list[float]] = {}
        self.lock = threading.Lock()

    def hit(self, key: str, limit: int, window: int, *, count: bool = True) -> int:
        """Returns 0 if allowed, else the seconds to wait. `count=False` only checks."""
        now = time.time()
        with self.lock:
            arr = [t for t in self.hits.get(key, []) if now - t < window]
            if len(arr) >= limit:
                self.hits[key] = arr
                return int(window - (now - arr[0])) + 1
            if count:
                arr.append(now)
            self.hits[key] = arr
            return 0

    def clear(self, key: str) -> None:
        with self.lock:
            self.hits.pop(key, None)


limiter = Limiter()


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _throttle(key: str, limit: int, window: int, what: str, *, count: bool = True) -> None:
    wait = limiter.hit(key, limit, window, count=count)
    if wait:
        raise HTTPException(429, f"Too many {what}. Try again in {max(1, wait // 60)} min.", headers={"Retry-After": str(wait)})


# --------------------------------------------------------------------------------- users + sessions
def _public(u: sqlite3.Row) -> dict:
    parts = [w for w in re.split(r"\s+", u["name"].strip()) if w]
    initials = ((parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")) if parts else "?").upper()
    return {
        "id": u["id"], "email": None if u["is_guest"] else u["email"], "name": u["name"], "is_guest": bool(u["is_guest"]),
        "created_at": u["created_at"], "country": u["country"], "city": u["city"], "use_cases": json.loads(u["use_cases"] or "[]"),
        "complaint_name": u["complaint_name"], "default_provider": u["default_provider"], "default_quick": bool(u["default_quick"]),
        "theme": u["theme"], "onboarded": bool(u["onboarded"]), "initials": initials, "hue": u["hue"],
    }


def _new_session(user_id: int, request: Request, response: Response, persistent: bool) -> None:
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    ttl = SESSION_PERSISTENT if persistent else SESSION_SHORT
    q("INSERT INTO sessions(user_id, token_hash, created_at, last_seen, expires_at, ua, ip) VALUES(?,?,?,?,?,?,?)",
      (user_id, hashlib.sha256(token.encode()).hexdigest(), now, now, now + ttl,
       request.headers.get("user-agent", "")[:200], client_ip(request)))
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(COOKIE, token, max_age=ttl if persistent else None, httponly=True, samesite="lax", secure=secure, path="/")


def session_user(request: Request) -> tuple[sqlite3.Row | None, sqlite3.Row | None]:
    token = request.cookies.get(COOKIE)
    if not token:
        return None, None
    now = int(time.time())
    s = q("SELECT * FROM sessions WHERE token_hash=? AND expires_at>?", (hashlib.sha256(token.encode()).hexdigest(), now)).fetchone()
    if not s:
        return None, None
    u = q("SELECT * FROM users WHERE id=?", (s["user_id"],)).fetchone()
    if not u:
        return None, None
    if now - s["last_seen"] > 300:
        q("UPDATE sessions SET last_seen=? WHERE id=?", (now, s["id"]))
    return u, s


def optional_user(request: Request) -> Optional[sqlite3.Row]:
    return session_user(request)[0]


async def require_user(request: Request) -> sqlite3.Row:
    # async on purpose: the ContextVar set here must be visible to the (async) endpoint that follows.
    u = optional_user(request)
    if not u:
        raise HTTPException(401, "Please sign in.")
    CURRENT_USER.set(u["id"])
    return u


def _delete_user_data(conn: sqlite3.Connection, user_id: int) -> None:
    owned = [r["audit_id"] for r in conn.execute("SELECT audit_id FROM audit_owner WHERE user_id=?", (user_id,))]
    for aid in owned:
        shutil.rmtree(settings.runs_dir / aid, ignore_errors=True)
    from app import sessions as shop_sessions  # the user's saved shop sign-ins go too
    shop_sessions.forget_all(user_id)
    conn.execute("DELETE FROM users WHERE id=?", (user_id,))  # cascades to sessions + audit_owner


# --------------------------------------------------------------------------------- audit ownership
def set_owner(audit_id: str, user_id: int | None) -> None:
    if user_id:
        q("INSERT OR REPLACE INTO audit_owner(audit_id, user_id) VALUES(?,?)", (audit_id, user_id))


def owner_of(audit_id: str) -> int | None:
    r = q("SELECT user_id FROM audit_owner WHERE audit_id=?", (audit_id,)).fetchone()
    return r["user_id"] if r else None


def can_view(audit_id: str, user_id: int) -> bool:
    """Audits with no owner are shared samples; owned audits are private to their owner."""
    o = owner_of(audit_id)
    return o is None or o == user_id


def owned_ids(user_id: int) -> set[str]:
    return {r["audit_id"] for r in q("SELECT audit_id FROM audit_owner WHERE user_id=?", (user_id,))}


# --------------------------------------------------------------------------------- API
router = APIRouter(prefix="/api")


class SignUp(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    email: str = Field(max_length=120)
    password: str = Field(max_length=200)
    remember: bool = True


class SignIn(BaseModel):
    email: str = Field(max_length=120)
    password: str = Field(max_length=200)
    remember: bool = True


class ProfilePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    country: str | None = None
    city: str | None = Field(default=None, max_length=60)
    use_cases: list[str] | None = None
    complaint_name: str | None = Field(default=None, max_length=80)
    default_provider: str | None = None
    default_quick: bool | None = None
    theme: str | None = None
    onboarded: bool | None = None


class PasswordChange(BaseModel):
    current: str = Field(max_length=200)
    new: str = Field(max_length=200)


class DeleteAccount(BaseModel):
    password: str = Field(default="", max_length=200)


def _clean_email(e: str) -> str:
    e = e.strip().lower()
    if not EMAIL_RE.match(e):
        raise HTTPException(422, "Enter a valid email address.")
    return e


@router.post("/auth/signup")
async def signup(body: SignUp, request: Request, response: Response):
    ip = client_ip(request)
    _throttle(f"signup:{ip}", 8, 3600, "sign-ups from this network")
    email = _clean_email(body.email)
    name = re.sub(r"\s+", " ", body.name).strip()
    if not name:
        raise HTTPException(422, "Enter your name.")
    problem = password_problem(body.password, email, name)
    if problem:
        raise HTTPException(422, problem)
    if q("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
        raise HTTPException(409, "An account with this email already exists. Sign in instead.")
    pw_hash = await asyncio.to_thread(hash_password, body.password)
    now = int(time.time())
    cur = q("INSERT INTO users(email, name, pw_hash, created_at, last_login, hue) VALUES(?,?,?,?,?,?)",
            (email, name, pw_hash, now, now, int(hashlib.md5(email.encode()).hexdigest(), 16) % 6))
    guest_user = optional_user(request)
    if guest_user and guest_user["is_guest"]:  # a demo visitor upgrading: their audits move to the new account
        q("UPDATE audit_owner SET user_id=? WHERE user_id=?", (cur.lastrowid, guest_user["id"]))
        q("DELETE FROM users WHERE id=?", (guest_user["id"],))  # cascades the guest's sessions
    _new_session(cur.lastrowid, request, response, body.remember)
    return _public(q("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone())


@router.post("/auth/login")
async def login(body: SignIn, request: Request, response: Response):
    ip, email = client_ip(request), body.email.strip().lower()
    _throttle(f"login-ip:{ip}", 40, 900, "sign-in attempts", count=False)
    _throttle(f"login:{ip}:{email}", 6, 900, "failed sign-ins", count=False)
    u = q("SELECT * FROM users WHERE email=? AND is_guest=0", (email,)).fetchone()
    ok = await asyncio.to_thread(verify_password, body.password, u["pw_hash"] if u else None)
    if not (u and ok):
        limiter.hit(f"login:{ip}:{email}", 6, 900)
        limiter.hit(f"login-ip:{ip}", 40, 900)
        raise HTTPException(401, "Incorrect email or password.")
    limiter.clear(f"login:{ip}:{email}")
    q("UPDATE users SET last_login=? WHERE id=?", (int(time.time()), u["id"]))
    _new_session(u["id"], request, response, body.remember)
    return _public(u)


@router.post("/auth/guest")
async def guest(request: Request, response: Response):
    _throttle(f"guest:{client_ip(request)}", 10, 3600, "demo sessions from this network")
    now = int(time.time())
    handle = secrets.token_hex(4)
    cur = q("INSERT INTO users(email, name, is_guest, created_at, last_login, onboarded, hue) VALUES(?,?,1,?,?,1,?)",
            (f"guest-{handle}@guest.invalid", "Guest", now, now, secrets.randbelow(6)))
    _new_session(cur.lastrowid, request, response, persistent=False)
    return _public(q("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone())


@router.post("/auth/logout")
def logout(request: Request, response: Response):
    _, s = session_user(request)
    if s:
        q("DELETE FROM sessions WHERE id=?", (s["id"],))
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/auth/me")
def me(user=Depends(require_user)):
    return _public(user)


@router.patch("/profile")
def update_profile(body: ProfilePatch, user=Depends(require_user)):
    data = body.model_dump(exclude_none=True)
    if "name" in data:
        data["name"] = re.sub(r"\s+", " ", data["name"]).strip()
        if not data["name"]:
            raise HTTPException(422, "Name cannot be empty.")
    if "country" in data and data["country"] not in COUNTRIES + [""]:
        raise HTTPException(422, "Unknown country.")
    if "use_cases" in data:
        data["use_cases"] = json.dumps([u for u in data["use_cases"] if u in USE_CASES][:6])
    if "default_provider" in data and data["default_provider"] not in ("", "opensource", "tinyfish"):
        raise HTTPException(422, "Unknown provider.")
    if "theme" in data and data["theme"] not in ("system", "light", "dark"):
        raise HTTPException(422, "Unknown theme.")
    for k in ("default_quick", "onboarded"):
        if k in data:
            data[k] = int(data[k])
    if data:
        cols = ", ".join(f"{k}=?" for k in data)
        q(f"UPDATE users SET {cols} WHERE id=?", (*data.values(), user["id"]))
    return _public(q("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone())


@router.post("/profile/password")
async def change_password(body: PasswordChange, request: Request, user=Depends(require_user)):
    if user["is_guest"]:
        raise HTTPException(400, "Create an account first.")
    _throttle(f"pwchange:{user['id']}", 6, 900, "password attempts")
    if not await asyncio.to_thread(verify_password, body.current, user["pw_hash"]):
        raise HTTPException(403, "Your current password is incorrect.")
    problem = password_problem(body.new, user["email"], user["name"])
    if problem:
        raise HTTPException(422, problem)
    if body.new == body.current:
        raise HTTPException(422, "Choose a password you have not used just now.")
    q("UPDATE users SET pw_hash=? WHERE id=?", (await asyncio.to_thread(hash_password, body.new), user["id"]))
    _, cur = session_user(request)  # sign out every other device, keep this one
    q("DELETE FROM sessions WHERE user_id=? AND id<>?", (user["id"], cur["id"]))
    return {"ok": True}


@router.get("/profile/sessions")
def list_sessions(request: Request, user=Depends(require_user)):
    _, cur = session_user(request)
    rows = q("SELECT * FROM sessions WHERE user_id=? AND expires_at>? ORDER BY last_seen DESC", (user["id"], int(time.time()))).fetchall()
    return [{"id": r["id"], "current": r["id"] == cur["id"], "created_at": r["created_at"], "last_seen": r["last_seen"],
             "ua": r["ua"], "ip": r["ip"]} for r in rows]


@router.delete("/profile/sessions/{sid}")
def revoke_session(sid: int, request: Request, user=Depends(require_user)):
    q("DELETE FROM sessions WHERE id=? AND user_id=?", (sid, user["id"]))
    return {"ok": True}


@router.post("/profile/sessions/revoke-others")
def revoke_others(request: Request, user=Depends(require_user)):
    _, cur = session_user(request)
    q("DELETE FROM sessions WHERE user_id=? AND id<>?", (user["id"], cur["id"]))
    return {"ok": True}


@router.delete("/profile")
async def delete_account(body: DeleteAccount, response: Response, user=Depends(require_user)):
    if not user["is_guest"]:
        _throttle(f"delacct:{user['id']}", 5, 900, "attempts")
        if not await asyncio.to_thread(verify_password, body.password, user["pw_hash"]):
            raise HTTPException(403, "Password is incorrect.")
    with _db_lock:
        _delete_user_data(db(), user["id"])
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}
