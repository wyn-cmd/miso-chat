# SQLite storage for the Miso Chat web app: accounts, sessions, usage and settings.
#
# Lives outside the character folders on purpose (the database sits in the data dir),
# so nothing here is walked by the terminal script's sync.
import hashlib
import hmac
import os
import secrets
import sqlite3
import time
import datetime
import pytz

from rp_core import pacific_date

REMEMBER_SECONDS = 30 * 24 * 3600      # "keep me signed in": renewed while the device keeps using the site
SHORT_SESSION_SECONDS = 12 * 3600      # without "keep me signed in"
ADMIN_NAME = "admin"
ADMIN_DEFAULT_PASSWORD = "admin"   # forced to change on first login

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    pw_hash TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    must_change INTEGER NOT NULL DEFAULT 0,
    disabled INTEGER NOT NULL DEFAULT 0,
    daily_cap INTEGER,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS usage (
    user_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    requests INTEGER NOT NULL DEFAULT 0,
    tokens INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day)
);
CREATE TABLE IF NOT EXISTS usage_daily (
    day TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    provider TEXT NOT NULL,
    requests INTEGER NOT NULL DEFAULT 0,
    tokens INTEGER NOT NULL DEFAULT 0,
    messages INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, user_id, provider)
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS prefs (
    user_id INTEGER NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (user_id, key)
);
"""

DEFAULT_SETTINGS = {
    "user_daily_cap": "100",     # Gemini calls per user per Pacific day
    "global_daily_cap": "800",   # all web users together, per Pacific day
    "rpm_limit": "25",           # all web users together, per minute (key is 30 RPM)
    "reply_min_tokens": "50",    # shortest reply length a user can pick, in tokens
    "reply_max_tokens": "1000",  # longest reply length a user can pick, in tokens
}


def hash_password(pw):
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(pw.encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${dk.hex()}"


def verify_password(pw, stored):
    try:
        _, n, r, p, salt_hex, dk_hex = stored.split("$")
        dk = hashlib.scrypt(pw.encode("utf-8"), salt=bytes.fromhex(salt_hex),
                            n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(dk, bytes.fromhex(dk_hex))
    except (ValueError, TypeError):
        return False


def _token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def recent_days(n):
    # The last n Pacific days, oldest first, as the 'dd/mm/yy' strings the usage tables store.
    today = datetime.datetime.now(pytz.timezone("US/Pacific")).date()
    return [(today - datetime.timedelta(days=i)).strftime("%d/%m/%y") for i in range(n - 1, -1, -1)]


def budget_days(kind):
    # Pacific day strings a token budget counts: daily = today, weekly = since Monday, monthly = since the 1st.
    today = datetime.datetime.now(pytz.timezone("US/Pacific")).date()
    span = {"daily": 1, "weekly": today.weekday() + 1, "monthly": today.day}[kind]
    return [(today - datetime.timedelta(days=i)).strftime("%d/%m/%y") for i in range(span)]


class Store:
    def __init__(self, path):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)
            ucols = {r["name"] for r in c.execute("PRAGMA table_info(users)")}
            for col in ("token_daily", "token_weekly", "token_monthly"):   # NULL = no budget
                if col not in ucols:
                    c.execute(f"ALTER TABLE users ADD COLUMN {col} INTEGER")
            if not c.execute("SELECT 1 FROM settings WHERE key = 'usage_daily_migrated'").fetchone():
                # usage before per-provider stats existed was all Gemini; it has no message counts
                c.execute("INSERT OR IGNORE INTO usage_daily (day, user_id, provider, requests, tokens, messages) "
                          "SELECT day, user_id, 'gemini', requests, tokens, 0 FROM usage")
                c.execute("INSERT INTO settings (key, value) VALUES ('usage_daily_migrated', '1')")
            cols = {r["name"] for r in c.execute("PRAGMA table_info(sessions)")}
            if "remember" not in cols:
                c.execute("ALTER TABLE sessions ADD COLUMN remember INTEGER NOT NULL DEFAULT 0")

    # ---- low-level

    def _conn(self):
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        return c

    def _run(self, sql, args=(), fetch=None):
        c = self._conn()
        try:
            with c:
                cur = c.execute(sql, args)
                if fetch == "one":
                    row = cur.fetchone()
                    return dict(row) if row else None
                if fetch == "all":
                    return [dict(r) for r in cur.fetchall()]
                return cur.lastrowid
        finally:
            c.close()

    # ---- accounts

    def ensure_admin(self):
        # Create admin/admin with a forced password change if there are no accounts yet.
        if self._run("SELECT COUNT(*) AS n FROM users", fetch="one")["n"] == 0:
            self.create_user(ADMIN_NAME, ADMIN_DEFAULT_PASSWORD, is_admin=True, must_change=True)

    def create_user(self, username, password, is_admin=False, must_change=True, daily_cap=None):
        try:
            return self._run(
                "INSERT INTO users (username, pw_hash, is_admin, must_change, daily_cap, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (username, hash_password(password), int(is_admin), int(must_change), daily_cap,
                 datetime.datetime.now().isoformat(timespec="seconds")))
        except sqlite3.IntegrityError:
            raise ValueError("that username is taken")

    def get_user(self, user_id):
        return self._run("SELECT * FROM users WHERE id = ?", (user_id,), fetch="one")

    def list_users(self):
        return self._run("SELECT * FROM users ORDER BY id", fetch="all")

    def authenticate(self, username, password):
        row = self._run("SELECT * FROM users WHERE username = ?", (username,), fetch="one")
        if row is None:
            # same cost as a real check, so the response time doesn't reveal account names
            hash_password(password)
            return None
        if not verify_password(password, row["pw_hash"]) or row["disabled"]:
            return None
        return row

    def set_password(self, user_id, password, must_change):
        self._run("UPDATE users SET pw_hash = ?, must_change = ? WHERE id = ?",
                  (hash_password(password), int(must_change), user_id))

    def update_user(self, user_id, **fields):
        allowed = {"is_admin", "disabled", "daily_cap", "token_daily", "token_weekly", "token_monthly"}
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"cannot change {sorted(bad)}")
        for key, val in fields.items():
            self._run(f"UPDATE users SET {key} = ? WHERE id = ?", (val, user_id))

    def count_enabled_admins(self):
        return self._run("SELECT COUNT(*) AS n FROM users WHERE is_admin = 1 AND disabled = 0",
                         fetch="one")["n"]

    # ---- sessions

    def new_session(self, user_id, remember=False):
        token = secrets.token_urlsafe(32)
        life = REMEMBER_SECONDS if remember else SHORT_SESSION_SECONDS
        self._run("INSERT INTO sessions (token_hash, user_id, expires_at, remember) VALUES (?, ?, ?, ?)",
                  (_token_hash(token), user_id, time.time() + life, int(bool(remember))))
        return token

    def session_user(self, token):
        if not token:
            return None
        now = time.time()
        row = self._run(
            "SELECT u.*, s.remember AS remember, s.expires_at AS expires_at FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token_hash = ? AND s.expires_at > ? AND u.disabled = 0",
            (_token_hash(token), now), fetch="one")
        # a remembered session slides forward once it is less than REMEMBER_SECONDS minus a day from expiry
        if row and row["remember"] and row["expires_at"] < now + REMEMBER_SECONDS - 86400:
            self._run("UPDATE sessions SET expires_at = ? WHERE token_hash = ?",
                      (now + REMEMBER_SECONDS, _token_hash(token)))
        return row

    def session_remembered(self, token):
        if not token:
            return False
        row = self._run("SELECT remember FROM sessions WHERE token_hash = ?", (_token_hash(token),), fetch="one")
        return bool(row and row["remember"])

    def end_session(self, token):
        self._run("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))

    def end_all_sessions(self, user_id):
        self._run("DELETE FROM sessions WHERE user_id = ?", (user_id,))

    # ---- per-user preferences

    def get_pref(self, user_id, key):
        row = self._run("SELECT value FROM prefs WHERE user_id = ? AND key = ?", (user_id, key), fetch="one")
        return row["value"] if row else None

    def set_pref(self, user_id, key, value):
        self._run("INSERT INTO prefs (user_id, key, value) VALUES (?, ?, ?) "
                  "ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value", (user_id, key, str(value)))

    # ---- usage and settings

    def add_usage(self, user_id, requests=0, tokens=0, provider="gemini", messages=0):
        # Add to today's totals: the account caps (usage) and the per-provider stats (usage_daily).
        day = pacific_date()
        self._run("INSERT INTO usage (user_id, day, requests, tokens) VALUES (?, ?, ?, ?) "
                  "ON CONFLICT(user_id, day) DO UPDATE SET "
                  "requests = requests + excluded.requests, tokens = tokens + excluded.tokens",
                  (user_id, day, requests, tokens))
        self._run("INSERT INTO usage_daily (day, user_id, provider, requests, tokens, messages) "
                  "VALUES (?, ?, ?, ?, ?, ?) "
                  "ON CONFLICT(day, user_id, provider) DO UPDATE SET "
                  "requests = requests + excluded.requests, tokens = tokens + excluded.tokens, "
                  "messages = messages + excluded.messages",
                  (day, user_id, provider, requests, tokens, messages))

    def usage_series(self, days, provider="all"):
        # Per-day totals for the given day strings (see recent_days), optionally for one provider.
        marks = ",".join("?" for _ in days)
        sql = (f"SELECT day, SUM(requests) AS requests, SUM(tokens) AS tokens, SUM(messages) AS messages "
               f"FROM usage_daily WHERE day IN ({marks})")
        args = list(days)
        if provider != "all":
            sql += " AND provider = ?"
            args.append(provider)
        return self._run(sql + " GROUP BY day", tuple(args), fetch="all")

    def usage_by_provider(self, days):
        marks = ",".join("?" for _ in days)
        return self._run(f"SELECT provider, SUM(requests) AS requests, SUM(tokens) AS tokens, "
                         f"SUM(messages) AS messages FROM usage_daily WHERE day IN ({marks}) GROUP BY provider",
                         tuple(days), fetch="all")

    def usage_tokens(self, user_id, days):
        # Tokens one account used across the given day strings (the account's own usage table).
        marks = ",".join("?" for _ in days)
        row = self._run(f"SELECT COALESCE(SUM(tokens), 0) AS n FROM usage WHERE user_id = ? AND day IN ({marks})",
                        (user_id, *days), fetch="one")
        return int(row["n"])

    def usage_users_on(self, day, provider="all"):
        sql = ("SELECT d.user_id, u.username, SUM(d.requests) AS requests, SUM(d.tokens) AS tokens, "
               "SUM(d.messages) AS messages FROM usage_daily d JOIN users u ON u.id = d.user_id WHERE d.day = ?")
        args = [day]
        if provider != "all":
            sql += " AND d.provider = ?"
            args.append(provider)
        return self._run(sql + " GROUP BY d.user_id, u.username ORDER BY requests DESC", tuple(args), fetch="all")

    def usage_today(self, user_id):
        row = self._run("SELECT requests, tokens FROM usage WHERE user_id = ? AND day = ?",
                        (user_id, pacific_date()), fetch="one")
        return row or {"requests": 0, "tokens": 0}

    def global_requests_today(self):
        return self._run("SELECT COALESCE(SUM(requests), 0) AS n FROM usage WHERE day = ?",
                         (pacific_date(),), fetch="one")["n"]

    def usage_today_all(self):
        rows = self._run("SELECT user_id, requests, tokens FROM usage WHERE day = ?",
                         (pacific_date(),), fetch="all")
        return {r["user_id"]: r for r in rows}

    def get_setting(self, key):
        row = self._run("SELECT value FROM settings WHERE key = ?", (key,), fetch="one")
        return row["value"] if row else DEFAULT_SETTINGS[key]

    def set_setting(self, key, value):
        self._run("INSERT INTO settings (key, value) VALUES (?, ?) "
                  "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(value)))

    def all_settings(self):
        return {k: self.get_setting(k) for k in DEFAULT_SETTINGS}
