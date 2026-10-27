# SQLite storage for the Miso Chat web app: accounts, sessions, usage and settings.
#
# Lives outside the character folders on purpose (the database sits in the data dir).
import hashlib
import hmac
import os
import secrets
import sqlite3
import sys
import threading
import time
import datetime
import pytz

from rp_core import pacific_date

REMEMBER_SECONDS = 30 * 24 * 3600      # "keep me signed in": renewed while the device keeps using the site
SHORT_SESSION_SECONDS = 12 * 3600      # without "keep me signed in"
PENDING_SECONDS = 300                  # time to finish a two-factor code after the password
SETUP_PENDING_SECONDS = 900            # a first set-up needs time to install an authenticator app

# Failed sign-ins for one username. A lock starts at each count; the next count is 5 more failures.
LOCK_STEPS = ((10, 15 * 60), (15, 30 * 60), (20, 60 * 60), (25, 2 * 60 * 60), (30, 4 * 60 * 60))
# Past the last step the lock repeats: every REPEAT_FAILS more wrong guesses lock the name again for REPEAT_SECONDS.
# There is no permanent lock, because one stranger could otherwise lock anyone out for good; guessing is still
# slowed to a handful of tries every few hours.
REPEAT_FAILS = 5
REPEAT_SECONDS = 4 * 3600
IP_LOCK_FAILS = 15     # consecutive wrong guesses from one address that block it for one account
# The throttle tables forget a name or address that has been quiet this long, and never hold more than
# THROTTLE_MAX_ROWS rows. Locked and blocked rows are kept until their lock ends or an admin clears them.
THROTTLE_IDLE_SECONDS = 30 * 24 * 3600
THROTTLE_MAX_ROWS = 50_000
THROTTLE_PRUNE_EVERY = 3600
HASH_N = 2 ** 16         # scrypt cost for new hashes; about 64 MB per check
# OpenSSL refuses scrypt above 32 MB unless told otherwise, and that refusal reads as a wrong password.
SCRYPT_MAXMEM = 128 * 1024 * 1024
ADMIN_NAME = "admin"

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
CREATE TABLE IF NOT EXISTS login_throttle (
    username TEXT PRIMARY KEY,
    fails INTEGER NOT NULL DEFAULT 0,
    locked_until REAL NOT NULL DEFAULT 0,
    permanent INTEGER NOT NULL DEFAULT 0,
    last_fail REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS login_ip_throttle (
    username TEXT NOT NULL,
    ip TEXT NOT NULL,
    fails INTEGER NOT NULL DEFAULT 0,
    blocked INTEGER NOT NULL DEFAULT 0,
    last_fail REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (username, ip)
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
CREATE TABLE IF NOT EXISTS usage_models (
    day TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    model TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT '',
    requests INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    cached_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    seconds REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (day, user_id, model)
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
CREATE TABLE IF NOT EXISTS checkins (
    user_id INTEGER NOT NULL,
    character TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0,
    mode TEXT NOT NULL DEFAULT 'at',
    at_min INTEGER NOT NULL DEFAULT 1200,
    start_min INTEGER NOT NULL DEFAULT 540,
    end_min INTEGER NOT NULL DEFAULT 1140,
    tz TEXT NOT NULL DEFAULT '',
    target_day TEXT NOT NULL DEFAULT '',
    target_min INTEGER NOT NULL DEFAULT -1,
    fired_day TEXT NOT NULL DEFAULT '',
    unread INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, character)
);
CREATE TABLE IF NOT EXISTS dreams (
    user_id INTEGER NOT NULL,
    character TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0,
    start_min INTEGER NOT NULL DEFAULT 120,
    end_min INTEGER NOT NULL DEFAULT 360,
    tz TEXT NOT NULL DEFAULT '',
    last_day TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (user_id, character)
);
CREATE TABLE IF NOT EXISTS moods (
    user_id INTEGER NOT NULL,
    character TEXT NOT NULL,
    energy REAL NOT NULL DEFAULT 80,
    affinity REAL NOT NULL DEFAULT 30,
    emotion TEXT NOT NULL DEFAULT 'calm',
    streak INTEGER NOT NULL DEFAULT 0,
    last_update REAL NOT NULL DEFAULT 0,
    last_user REAL NOT NULL DEFAULT 0,
    reached REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, character)
);
CREATE TABLE IF NOT EXISTS attitudes (
    user_id INTEGER NOT NULL,
    character TEXT NOT NULL,
    affection REAL NOT NULL DEFAULT 5,
    trust REAL NOT NULL DEFAULT 5,
    level TEXT NOT NULL DEFAULT 'stranger',
    exchanges INTEGER NOT NULL DEFAULT 0,
    since_bond INTEGER NOT NULL DEFAULT 0,
    last_fired TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (user_id, character)
);
CREATE TABLE IF NOT EXISTS backup_auth (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    verifier TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS char_messages (
    name TEXT PRIMARY KEY,
    total INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS favourites (
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, name)
);
"""

# Tiers: a monthly token cap per account (Pacific month). Admin accounts have no tier and no cap.
TIERS = ("free", "lite", "standard", "pro")
TIER_MONTHLY_TOKENS = {"free": 1_000_000, "lite": 10_000_000, "standard": 20_000_000, "pro": 100_000_000}

DEFAULT_SETTINGS = {
    "char_messages_backfilled_v2": "0",
    "user_daily_cap": "100",     # Gemini calls per user per Pacific day
    "global_daily_cap": "800",   # all web users together, per Pacific day
    "rpm_limit": "25",           # all web users together, per minute (key is 30 RPM)
    "reply_min_tokens": "50",    # shortest reply length a user can pick, in tokens
    "reply_max_tokens": "1000",  # longest reply length a user can pick, in tokens
    "memory_enabled": "1",       # memory bank (memory.md and pinned facts): 1 on, 0 off
    "tier_config": "",           # JSON: per tier monthly token cap, allowed providers, blocked models (admin Tiers page)
    "deepseek_prices": "",       # JSON: overrides the DeepSeek rate table in web/pricing.py (per million tokens)
    "palace_enabled": "1",       # Mind Palace keyword entries: 1 on, 0 off
    "episodes_enabled": "1",     # episodic memory, recalled by keyword: 1 on, 0 off
    "dreams_enabled": "0",       # autonomous dreaming in quiet hours: 1 on, 0 off (off until an admin turns it on)
    "dream_every_days": "1",     # dreams run once every this many days at the least, 1 to 30
    "dream_daily_cap": "40",     # most model calls the dreams may make across the server each Pacific day
    "dreams_in_novels": "1",     # novels dream on their own switch, apart from dreams_enabled: 1 on, 0 off (on by default)
    "mood_enabled": "0",         # character mood (energy, affinity, emotion) and lonely check-ins: 1 on, 0 off
    "attitude_enabled": "0",     # relationship with the user (affection, trust, five levels): 1 on, 0 off
    "event_memory_enabled": "0", # event memory graph, written by dreams and recalled into prompts: 1 on, 0 off
    "memory_policy_enabled": "0", # learned memory retention: score self-written memories by outcome, 1 on, 0 off
    "budget_webhook_url": "",    # where budget alerts (80% and 100% of a window) are posted as JSON; empty = off
}


def hash_password(pw):
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(pw.encode("utf-8"), salt=salt, n=HASH_N, r=8, p=1, dklen=32, maxmem=SCRYPT_MAXMEM)
    return f"scrypt${HASH_N}$8$1${salt.hex()}${dk.hex()}"


def verify_password(pw, stored):
    try:
        _, n, r, p, salt_hex, dk_hex = stored.split("$")
        dk = hashlib.scrypt(pw.encode("utf-8"), salt=bytes.fromhex(salt_hex),
                            n=int(n), r=int(r), p=int(p), dklen=32, maxmem=SCRYPT_MAXMEM)
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


def needs_rehash(stored):
    # True when a stored hash was made at a lower scrypt cost than HASH_N.
    try:
        _, n, _, _, _, _ = stored.split("$")
        return int(n) < HASH_N
    except (ValueError, TypeError):
        return False


# The user id under which server-side calls that belong to no account are recorded. Real accounts start at 1.
SYSTEM_USER = 0


class Store:
    def __init__(self, path):
        self.path = path
        self._throttle_lock = threading.Lock()
        self._pruned_at = 0.0
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)
            for table in ("login_throttle", "login_ip_throttle"):
                # databases made before the idle timestamp existed: old rows read as idle since the epoch
                tcols = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
                if "last_fail" not in tcols:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN last_fail REAL NOT NULL DEFAULT 0")
            ucols = {r["name"] for r in c.execute("PRAGMA table_info(users)")}
            for col in ("token_daily", "token_weekly", "token_monthly"):   # NULL = no budget
                if col not in ucols:
                    c.execute(f"ALTER TABLE users ADD COLUMN {col} INTEGER")
            if "tier" not in ucols:
                # existing non-admin accounts start on lite, so the lite monthly cap applies to them
                c.execute("ALTER TABLE users ADD COLUMN tier TEXT")
                c.execute("UPDATE users SET tier = 'lite' WHERE is_admin = 0")
            if not c.execute("SELECT 1 FROM settings WHERE key = 'usage_daily_migrated'").fetchone():
                # usage before per-provider stats existed was all Gemini; it has no message counts
                c.execute("INSERT OR IGNORE INTO usage_daily (day, user_id, provider, requests, tokens, messages) "
                          "SELECT day, user_id, 'gemini', requests, tokens, 0 FROM usage")
                c.execute("INSERT INTO settings (key, value) VALUES ('usage_daily_migrated', '1')")
            dcols = {r["name"] for r in c.execute("PRAGMA table_info(usage_daily)")}
            for col in ("input_tokens", "cached_tokens", "output_tokens"):
                if col not in dcols:
                    # older rows keep 0: the split was not recorded then, and is not guessed now
                    c.execute(f"ALTER TABLE usage_daily ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0")
            if "seconds" not in dcols:
                c.execute("ALTER TABLE usage_daily ADD COLUMN seconds REAL NOT NULL DEFAULT 0")
            cols = {r["name"] for r in c.execute("PRAGMA table_info(sessions)")}
            if "remember" not in cols:
                c.execute("ALTER TABLE sessions ADD COLUMN remember INTEGER NOT NULL DEFAULT 0")
            if "stage" not in cols:
                # full sessions are the normal kind; pending ones only finish a two-factor step
                c.execute("ALTER TABLE sessions ADD COLUMN stage TEXT NOT NULL DEFAULT 'full'")
            ucols = {r["name"] for r in c.execute("PRAGMA table_info(users)")}
            for col, ddl in (("totp_secret", "TEXT"), ("totp_enabled", "INTEGER NOT NULL DEFAULT 0"),
                             ("totp_confirmed", "INTEGER NOT NULL DEFAULT 0"),
                             ("totp_step", "INTEGER NOT NULL DEFAULT -1"),
                             ("totp_required", "INTEGER NOT NULL DEFAULT 0")):
                if col not in ucols:
                    c.execute(f"ALTER TABLE users ADD COLUMN {col} {ddl}")

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
        # On the first start with no accounts, create the admin with a random one-time password and print it once.
        # A fixed default let whoever reached a new install first sign in as the admin.
        if self._run("SELECT COUNT(*) AS n FROM users", fetch="one")["n"] == 0:
            password = secrets.token_urlsafe(12)
            self.create_user(ADMIN_NAME, password, is_admin=True, must_change=True)
            print(f"[web] First start: created the admin account '{ADMIN_NAME}' with the one-time password "
                  f"{password}. Sign in and change it before the site is reachable from anywhere else.",
                  file=sys.stderr, flush=True)

    def create_user(self, username, password, is_admin=False, must_change=True, daily_cap=None, tier=None):
        try:
            return self._run(
                "INSERT INTO users (username, pw_hash, is_admin, must_change, daily_cap, created_at, tier) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (username, hash_password(password), int(is_admin), int(must_change), daily_cap,
                 datetime.datetime.now().isoformat(timespec="seconds"), tier))
        except sqlite3.IntegrityError:
            raise ValueError("that username is taken")

    def get_user(self, user_id):
        return self._run("SELECT * FROM users WHERE id = ?", (user_id,), fetch="one")

    def find_user(self, username):
        return self._run("SELECT * FROM users WHERE username = ?", (username,), fetch="one")

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

    def upgrade_hash(self, user_id, password):
        # Re-hash at the current cost without touching must_change.
        self._run("UPDATE users SET pw_hash = ? WHERE id = ?", (hash_password(password), user_id))

    def update_user(self, user_id, **fields):
        allowed = {"is_admin", "disabled", "daily_cap", "token_daily", "token_weekly", "token_monthly", "tier"}
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"cannot change {sorted(bad)}")
        for key, val in fields.items():
            self._run(f"UPDATE users SET {key} = ? WHERE id = ?", (val, user_id))

    def count_enabled_admins(self):
        return self._run("SELECT COUNT(*) AS n FROM users WHERE is_admin = 1 AND disabled = 0",
                         fetch="one")["n"]

    # ---- sign-in lockout
    # Failures count per username whether or not the account exists, so a refusal never shows which
    # names are real. The count resets only on a successful sign-in or an admin unlock.

    def login_block(self, username, now=None):
        # None when a sign-in may be checked; else ("permanent", None) or ("timed", seconds_left).
        now = time.time() if now is None else now
        row = self._run("SELECT locked_until, permanent FROM login_throttle WHERE username = ?",
                        (username.strip().lower(),), fetch="one")
        if row is None:
            return None
        if row["permanent"]:
            return ("permanent", None)
        if row["locked_until"] > now:
            return ("timed", row["locked_until"] - now)
        return None

    def record_failed_login(self, username, now=None):
        # Counts one failed check and returns the lock now in force, in the same shape as login_block.
        now = time.time() if now is None else now
        key = username.strip().lower()
        with self._throttle_lock:
            self._prune_throttles(now)
            row = self._run("SELECT fails, locked_until, permanent FROM login_throttle WHERE username = ?",
                            (key,), fetch="one") or {"fails": 0, "locked_until": 0.0, "permanent": 0}
            fails = row["fails"] + 1
            locked_until, permanent = row["locked_until"], 0
            for threshold, seconds in LOCK_STEPS:
                if fails == threshold:
                    locked_until = now + seconds
            last_step = LOCK_STEPS[-1][0]
            if fails > last_step and (fails - last_step) % REPEAT_FAILS == 0:
                locked_until = now + REPEAT_SECONDS
            self._run("INSERT INTO login_throttle (username, fails, locked_until, permanent, last_fail) "
                      "VALUES (?, ?, ?, ?, ?) "
                      "ON CONFLICT(username) DO UPDATE SET fails = excluded.fails, "
                      "locked_until = excluded.locked_until, permanent = excluded.permanent, "
                      "last_fail = excluded.last_fail",
                      (key, fails, locked_until, permanent, now))
        return self.login_block(key, now)

    def _prune_throttles(self, now):
        # Called with the throttle lock held. Once an hour: drop quiet names and addresses, then keep each table
        # under THROTTLE_MAX_ROWS by dropping the quietest unlocked rows. Without this, failed sign-ins for random
        # names would grow the database for ever.
        if now - self._pruned_at < THROTTLE_PRUNE_EVERY:
            return
        self._pruned_at = now
        cut = now - THROTTLE_IDLE_SECONDS
        self._run("DELETE FROM login_throttle WHERE last_fail < ? AND locked_until <= ?", (cut, now))
        self._run("DELETE FROM login_ip_throttle WHERE last_fail < ? AND blocked = 0", (cut,))
        for table, keep in (("login_throttle", "locked_until <= ?"), ("login_ip_throttle", "blocked = 0")):
            args = (now,) if table == "login_throttle" else ()
            over = self._run(f"SELECT COUNT(*) AS n FROM {table}", fetch="one")["n"] - THROTTLE_MAX_ROWS
            if over > 0:
                self._run(f"DELETE FROM {table} WHERE rowid IN (SELECT rowid FROM {table} WHERE {keep} "
                          f"ORDER BY last_fail LIMIT ?)", args + (over,))

    def clear_login_lock(self, username):
        self._run("DELETE FROM login_throttle WHERE username = ?", (username.strip().lower(),))

    def ip_blocked(self, username, ip):
        row = self._run("SELECT blocked FROM login_ip_throttle WHERE username = ? AND ip = ?",
                        (username.strip().lower(), ip), fetch="one")
        return bool(row and row["blocked"])

    def record_failed_ip(self, username, ip, now=None):
        # Counts one wrong guess from this address for this account; True once the address is blocked.
        key = username.strip().lower()
        now = time.time() if now is None else now
        with self._throttle_lock:
            self._prune_throttles(now)
            row = self._run("SELECT fails, blocked FROM login_ip_throttle WHERE username = ? AND ip = ?",
                            (key, ip), fetch="one")
            fails = (row["fails"] if row else 0) + 1
            blocked = 1 if (fails >= IP_LOCK_FAILS or (row and row["blocked"])) else 0
            self._run("INSERT INTO login_ip_throttle (username, ip, fails, blocked, last_fail) "
                      "VALUES (?, ?, ?, ?, ?) "
                      "ON CONFLICT(username, ip) DO UPDATE SET fails = excluded.fails, blocked = excluded.blocked, "
                      "last_fail = excluded.last_fail",
                      (key, ip, fails, blocked, now))
        return bool(blocked)

    def clear_ip_throttle(self, username, ip):
        self._run("DELETE FROM login_ip_throttle WHERE username = ? AND ip = ?", (username.strip().lower(), ip))

    def unlock_account(self, username):
        # Admin or server unlock: clears the account lock and every address block on that account.
        self.clear_login_lock(username)
        self._run("DELETE FROM login_ip_throttle WHERE username = ?", (username.strip().lower(),))

    def blocked_ips_all(self):
        out = {}
        for r in self._run("SELECT username, ip FROM login_ip_throttle WHERE blocked = 1", fetch="all"):
            out.setdefault(r["username"], []).append(r["ip"])
        return out

    def login_throttle_all(self):
        return {r["username"]: r for r in self._run("SELECT * FROM login_throttle", fetch="all")}

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
            "WHERE s.token_hash = ? AND s.expires_at > ? AND u.disabled = 0 AND s.stage = 'full'",
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
        # A sign-out with no session cookie (already expired, or cleared) has nothing to end.
        if not token:
            return
        self._run("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))

    def end_all_sessions(self, user_id):
        self._run("DELETE FROM sessions WHERE user_id = ?", (user_id,))

    # ---- two-factor sign-in
    # totp_secret is sealed with the server key (web/secure.py). totp_enabled is the account's choice or an
    # admin's; totp_required is set by an admin and only an admin can lift it. totp_confirmed is set once the
    # app's code has been entered, so a new account signs in with the password until it sets up the app.

    def set_totp(self, user_id, sealed, enabled, confirmed, step=-1, required=None):
        if required is None:
            self._run("UPDATE users SET totp_secret = ?, totp_enabled = ?, totp_confirmed = ?, totp_step = ? "
                      "WHERE id = ?", (sealed, int(enabled), int(confirmed), step, user_id))
        else:
            self._run("UPDATE users SET totp_secret = ?, totp_enabled = ?, totp_confirmed = ?, totp_step = ?, "
                      "totp_required = ? WHERE id = ?", (sealed, int(enabled), int(confirmed), step, int(required), user_id))

    def set_totp_step(self, user_id, step):
        self._run("UPDATE users SET totp_step = ? WHERE id = ?", (step, user_id))

    def set_totp_required(self, user_id, required):
        # Requiring it enables the account's two-factor sign-in; lifting it leaves an account's own setting alone.
        self._run("UPDATE users SET totp_required = ?, totp_enabled = CASE WHEN ? THEN 1 ELSE totp_enabled END "
                  "WHERE id = ?", (int(required), int(required), user_id))

    def new_pending_session(self, user_id, stage, remember=False):
        # Short-lived; session_user ignores it, so it can only finish a two-factor step.
        token = secrets.token_urlsafe(32)
        life = SETUP_PENDING_SECONDS if stage == "setup" else PENDING_SECONDS
        self._run("INSERT INTO sessions (token_hash, user_id, expires_at, remember, stage) VALUES (?, ?, ?, ?, ?)",
                  (_token_hash(token), user_id, time.time() + life, int(bool(remember)), stage))
        return token

    def pending_user(self, token, stage):
        if not token:
            return None
        return self._run("SELECT u.*, s.remember AS remember FROM sessions s JOIN users u ON u.id = s.user_id "
                         "WHERE s.token_hash = ? AND s.stage = ? AND s.expires_at > ? AND u.disabled = 0",
                         (_token_hash(token), stage, time.time()), fetch="one")

    def promote_session(self, token, remember):
        life = REMEMBER_SECONDS if remember else SHORT_SESSION_SECONDS
        self._run("UPDATE sessions SET stage = 'full', expires_at = ? WHERE token_hash = ?",
                  (time.time() + life, _token_hash(token)))

    # ---- backup password: one verifier, set once by an admin with two-factor sign-in on

    def backup_verifier(self):
        row = self._run("SELECT verifier FROM backup_auth WHERE id = 1", fetch="one")
        return row["verifier"] if row else None

    def set_backup_verifier_once(self, verifier):
        # The first set wins; a later attempt changes nothing and returns False.
        c = self._conn()
        try:
            with c:
                cur = c.execute("INSERT OR IGNORE INTO backup_auth (id, verifier) VALUES (1, ?)", (verifier,))
                return cur.rowcount == 1
        finally:
            c.close()

    # ---- per-user preferences

    def get_pref(self, user_id, key):
        row = self._run("SELECT value FROM prefs WHERE user_id = ? AND key = ?", (user_id, key), fetch="one")
        return row["value"] if row else None

    def set_pref(self, user_id, key, value):
        self._run("INSERT INTO prefs (user_id, key, value) VALUES (?, ?, ?) "
                  "ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value", (user_id, key, str(value)))

    # ---- usage and settings

    def add_usage(self, user_id, requests=0, tokens=0, provider="gemini", messages=0,
                  input_tokens=0, cached_tokens=0, output_tokens=0, seconds=0.0, model=None):
        # Add to today's totals: the account caps (usage), the per-provider stats (usage_daily), and the
        # per-model stats (usage_models, when the model is known). Cached tokens are part of the input.
        day = pacific_date()
        self._run("INSERT INTO usage (user_id, day, requests, tokens) VALUES (?, ?, ?, ?) "
                  "ON CONFLICT(user_id, day) DO UPDATE SET "
                  "requests = requests + excluded.requests, tokens = tokens + excluded.tokens",
                  (user_id, day, requests, tokens))
        self._add_daily(day, user_id, requests, tokens, provider, messages, input_tokens, cached_tokens,
                        output_tokens, seconds, model)

    def add_system_usage(self, requests=0, tokens=0, provider="gemini", input_tokens=0, cached_tokens=0,
                         output_tokens=0, seconds=0.0, model=None):
        # Calls the server makes for no account (auto profiles, card drafts). They go into the same daily and
        # per-model tables under SYSTEM_USER, so the stats total includes them, but never into the usage table
        # the account caps read, so no one's quota moves.
        self._add_daily(pacific_date(), SYSTEM_USER, requests, tokens, provider, 0, input_tokens, cached_tokens,
                        output_tokens, seconds, model)

    def _add_daily(self, day, user_id, requests, tokens, provider, messages, input_tokens, cached_tokens,
                   output_tokens, seconds, model):
        self._run("INSERT INTO usage_daily (day, user_id, provider, requests, tokens, messages, "
                  "input_tokens, cached_tokens, output_tokens, seconds) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                  "ON CONFLICT(day, user_id, provider) DO UPDATE SET "
                  "requests = requests + excluded.requests, tokens = tokens + excluded.tokens, "
                  "messages = messages + excluded.messages, "
                  "input_tokens = input_tokens + excluded.input_tokens, "
                  "cached_tokens = cached_tokens + excluded.cached_tokens, "
                  "output_tokens = output_tokens + excluded.output_tokens, "
                  "seconds = seconds + excluded.seconds",
                  (day, user_id, provider, requests, tokens, messages,
                   input_tokens, cached_tokens, output_tokens, seconds))
        if model:
            self._run("INSERT INTO usage_models (day, user_id, model, provider, requests, input_tokens, "
                      "cached_tokens, output_tokens, seconds) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                      "ON CONFLICT(day, user_id, model) DO UPDATE SET "
                      "provider = excluded.provider, requests = requests + excluded.requests, "
                      "input_tokens = input_tokens + excluded.input_tokens, "
                      "cached_tokens = cached_tokens + excluded.cached_tokens, "
                      "output_tokens = output_tokens + excluded.output_tokens, "
                      "seconds = seconds + excluded.seconds",
                      (day, user_id, model, provider, requests, input_tokens, cached_tokens, output_tokens, seconds))

    def usage_breakdown(self, days, provider="all"):
        # Input, cached input, output and seconds over the given day strings, per provider.
        marks = ",".join("?" for _ in days)
        sql = (f"SELECT provider, SUM(input_tokens) AS input_tokens, SUM(cached_tokens) AS cached_tokens, "
               f"SUM(output_tokens) AS output_tokens, SUM(seconds) AS seconds "
               f"FROM usage_daily WHERE day IN ({marks})")
        args = list(days)
        if provider != "all":
            sql += " AND provider = ?"
            args.append(provider)
        return self._run(sql + " GROUP BY provider", tuple(args), fetch="all")

    def usage_model_totals(self, days, provider="all"):
        # Per-model input, cached input, output and seconds over the given day strings.
        marks = ",".join("?" for _ in days)
        sql = (f"SELECT model, provider, SUM(requests) AS requests, SUM(input_tokens) AS input_tokens, "
               f"SUM(cached_tokens) AS cached_tokens, SUM(output_tokens) AS output_tokens, "
               f"SUM(seconds) AS seconds FROM usage_models WHERE day IN ({marks})")
        args = list(days)
        if provider != "all":
            sql += " AND provider = ?"
            args.append(provider)
        return self._run(sql + " GROUP BY model, provider ORDER BY model", tuple(args), fetch="all")

    def usage_series(self, days, provider="all"):
        # Per-day totals for the given day strings (see recent_days), optionally for one provider.
        marks = ",".join("?" for _ in days)
        sql = (f"SELECT day, SUM(requests) AS requests, SUM(tokens) AS tokens, SUM(messages) AS messages, "
               f"SUM(input_tokens) AS input_tokens, SUM(cached_tokens) AS cached_tokens, "
               f"SUM(output_tokens) AS output_tokens, SUM(seconds) AS seconds "
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

    def bump_char_message(self, name, n=1):
        self._run("INSERT INTO char_messages (name, total) VALUES (?, ?) "
                  "ON CONFLICT(name) DO UPDATE SET total = total + excluded.total", (name, n))

    def clear_char_messages(self):
        self._run("DELETE FROM char_messages")

    def char_message_totals(self):
        rows = self._run("SELECT name, total FROM char_messages", fetch="all")
        return {r["name"]: r["total"] for r in rows}

    def favourite_counts(self):
        rows = self._run("SELECT name, COUNT(*) AS n FROM favourites GROUP BY name", fetch="all")
        return {r["name"]: r["n"] for r in rows}

    def favourites_of(self, user_id):
        rows = self._run("SELECT name FROM favourites WHERE user_id = ?", (user_id,), fetch="all")
        return {r["name"] for r in rows}

    def set_favourite(self, user_id, name, on):
        if on:
            self._run("INSERT OR IGNORE INTO favourites (user_id, name, created_at) VALUES (?, ?, ?)",
                      (user_id, name, datetime.datetime.now().isoformat(timespec="seconds")))
        else:
            self._run("DELETE FROM favourites WHERE user_id = ? AND name = ?", (user_id, name))

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

    # Scheduled check-ins: one row per account and character. The columns are set through checkin_update.
    CHECKIN_FIELDS = ("enabled", "mode", "at_min", "start_min", "end_min", "tz", "target_day", "target_min",
                      "fired_day", "unread")

    def checkin_get(self, user_id, character):
        return self._run("SELECT * FROM checkins WHERE user_id = ? AND character = ?",
                         (user_id, character), fetch="one")

    def checkin_update(self, user_id, character, **fields):
        bad = set(fields) - set(self.CHECKIN_FIELDS)
        if bad:
            raise ValueError(f"unknown check-in fields: {sorted(bad)}")
        self._run("INSERT OR IGNORE INTO checkins (user_id, character) VALUES (?, ?)", (user_id, character))
        if fields:
            cols = ", ".join(f"{k} = ?" for k in fields)
            self._run(f"UPDATE checkins SET {cols} WHERE user_id = ? AND character = ?",
                      (*fields.values(), user_id, character))

    def checkin_add_unread(self, user_id, character):
        self.checkin_update(user_id, character)
        self._run("UPDATE checkins SET unread = unread + 1 WHERE user_id = ? AND character = ?", (user_id, character))

    def checkin_unread_for(self, user_id):
        rows = self._run("SELECT character, unread FROM checkins WHERE user_id = ? AND unread > 0",
                         (user_id,), fetch="all")
        return {r["character"]: r["unread"] for r in rows}

    def checkin_enabled_rows(self):
        return self._run("SELECT * FROM checkins WHERE enabled = 1", fetch="all")

    # Autonomous dreaming: one row per account and character. Columns are set through dream_update.
    DREAM_FIELDS = ("enabled", "start_min", "end_min", "tz", "last_day")

    def dream_get(self, user_id, character):
        return self._run("SELECT * FROM dreams WHERE user_id = ? AND character = ?", (user_id, character), fetch="one")

    def dream_update(self, user_id, character, **fields):
        bad = set(fields) - set(self.DREAM_FIELDS)
        if bad:
            raise ValueError(f"unknown dream fields: {sorted(bad)}")
        self._run("INSERT OR IGNORE INTO dreams (user_id, character) VALUES (?, ?)", (user_id, character))
        if fields:
            cols = ", ".join(f"{k} = ?" for k in fields)
            self._run(f"UPDATE dreams SET {cols} WHERE user_id = ? AND character = ?",
                      (*fields.values(), user_id, character))

    def dream_enabled_rows(self):
        return self._run("SELECT * FROM dreams WHERE enabled = 1", fetch="all")

    # Character mood: one row per account and character. The fields are set as a whole through mood_save.
    MOOD_FIELDS = ("energy", "affinity", "emotion", "streak", "last_update", "last_user", "reached")

    def mood_get(self, user_id, character):
        return self._run("SELECT * FROM moods WHERE user_id = ? AND character = ?", (user_id, character), fetch="one")

    def mood_save(self, user_id, character, state):
        cols = ", ".join(self.MOOD_FIELDS)
        marks = ", ".join("?" for _ in self.MOOD_FIELDS)
        self._run(f"INSERT OR REPLACE INTO moods (user_id, character, {cols}) VALUES (?, ?, {marks})",
                  (user_id, character, *(state[k] for k in self.MOOD_FIELDS)))

    def mood_rows(self):
        return self._run("SELECT * FROM moods", fetch="all")

    # Attitude: one row per account and character. The row is written whole through attitude_save.
    ATTITUDE_FIELDS = ("affection", "trust", "level", "exchanges", "since_bond", "last_fired")

    def attitude_get(self, user_id, character):
        return self._run("SELECT * FROM attitudes WHERE user_id = ? AND character = ?", (user_id, character), fetch="one")

    def attitude_save(self, user_id, character, row):
        cols = ", ".join(self.ATTITUDE_FIELDS)
        marks = ", ".join("?" for _ in self.ATTITUDE_FIELDS)
        self._run(f"INSERT OR REPLACE INTO attitudes (user_id, character, {cols}) VALUES (?, ?, {marks})",
                  (user_id, character, *(row[k] for k in self.ATTITUDE_FIELDS)))

    # A stored value for a key that has no default (per-account state, such as the last budget alert sent).
    def get_state(self, key):
        row = self._run("SELECT value FROM settings WHERE key = ?", (key,), fetch="one")
        return row["value"] if row else ""

    def all_settings(self):
        return {k: self.get_setting(k) for k in DEFAULT_SETTINGS}
