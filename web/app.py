# Miso Chat web app: FastAPI front end over rp_core.Engine.
#
# Run from the project folder (the engine uses paths relative to the character root):
#     python -m uvicorn web.app:make_app --factory --host 127.0.0.1 --port 8000
#
# Environment:
#     GEMINI_API_KEY          required
#     MISO_DATA_DIR           app.db and per-user chat state (default ./data)
#     MISO_COOKIE_SECURE      defaults to 1; set to 0 only for plain-http testing on a private network
import collections
import hashlib
import ipaddress
import json
import math
import os
import queue
import re
import shutil
import sys
import threading
import time
from dataclasses import dataclass

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from google import genai
from rp_core import Engine, MAX_REGENERATIONS, SOLO_WINDOW, GROUP_WINDOW, SOLO_TEMPERATURE, GROUP_TEMPERATURE
from charname import display_for, folder_for
import sandbox
import llm_backends
from web import providers, editor, profiles, recommend, transfer, ocr, palace, draft, branches, private_groups, secure, backup
from web.store import REMEMBER_SECONDS, Store, DEFAULT_SETTINGS, budget_days, recent_days, verify_password, needs_rehash

HERE = os.path.dirname(os.path.abspath(__file__))
CHAR_ROOT = os.path.dirname(HERE)
# VERSION sits at the project root. The terminal client compares its major.minor with this.
APP_VERSION = open(os.path.join(CHAR_ROOT, "VERSION"), encoding="utf-8").read().strip() if os.path.isfile(os.path.join(CHAR_ROOT, "VERSION")) else "unknown"

# Read the project's .env (if any) before the settings below are read.
from dotenv_loader import load_dotenv  # noqa: E402
load_dotenv(os.path.join(CHAR_ROOT, ".env"))
# Two-factor secrets are sealed with MISO_SECRET_KEY. Make one on first start and keep it in .env, so
# every restart can open them. If .env can't be written, the two-factor routes refuse instead of guessing.
if not os.environ.get("MISO_SECRET_KEY"):
    _env_path = os.path.join(CHAR_ROOT, ".env")
    try:
        _before = open(_env_path, encoding="utf-8").read() if os.path.exists(_env_path) else ""
        _key = secure.new_server_key()
        with open(_env_path, "a", encoding="utf-8") as _f:
            _f.write(("" if not _before or _before.endswith("\n") else "\n") + "MISO_SECRET_KEY=" + _key + "\n")
        os.environ["MISO_SECRET_KEY"] = _key
    except OSError:
        pass
DATA_DIR = os.environ.get("MISO_DATA_DIR") or os.environ.get("ROLEPLAY_DATA_DIR") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
COOKIE = "rp_session"
COOKIE_SECURE = (os.environ.get("MISO_COOKIE_SECURE") or os.environ.get("ROLEPLAY_COOKIE_SECURE") or "1") != "0"   # on unless turned off for plain-http testing
MAX_MESSAGE = 4000
MIN_PASSWORD = 10
USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{2,32}$")
TOKEN_DISPLAY_LIMIT = 1_000_000
MEMORY_LIMIT = 20_000        # characters allowed in memory.md or pinned.md
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
       "base-uri 'none'; form-action 'self'")


def log(msg):
    print(f"[web] {msg}", file=sys.stderr, flush=True)


COLOUR_HEX = {
    "crimson": "#dc2f4a", "scarlet": "#ff2d55", "red": "#e5484d", "pink": "#f472b6", "magenta": "#e879f9",
    "orange": "#ff8c42", "amber": "#ffb000", "gold": "#d4a72c", "yellow": "#f5d33d", "lime": "#a3e635",
    "green": "#3cb371", "teal": "#14b8a6", "cyan": "#22d3ee", "electric blue": "#2d8cff", "blue": "#3b82f6",
    "deep blue": "#1e3a8a", "navy": "#1e3a8a", "purple": "#9b5de5", "dark purple": "#4b2a6b", "violet": "#8b5cf6",
    "brown": "#a0623c", "sienna": "#a0522d", "silver": "#c0c0c0", "white": "#f1f5f9", "black": "#2b2725",
    "grey": "#8a8f98", "gray": "#8a8f98",
}
NEUTRAL_HEX = "#8a8f98"


def colour_of(root, folder):
    # Hex colour from the Colour: line in character.md (first colour when several), or None if there is no line.
    try:
        with open(os.path.join(root, folder, "character.md"), encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    for line in lines:
        m = re.match(r"\s*[-*]?\s*colou?r\s*:\s*(.+)$", line, re.I)
        if m:
            raw_col = m.group(1).split("/")[0].strip().lower()
            if re.fullmatch(r"#[0-9a-f]{6}", raw_col):
                return raw_col
            return COLOUR_HEX.get(raw_col, NEUTRAL_HEX)
    return None


def load_personas(root):
    # Persona folders from personas.json. Personas are never offered as characters to chat with.
    try:
        with open(os.path.join(root, "personas.json"), encoding="utf-8") as f:
            names = json.load(f).get("personas", [])
    except (OSError, ValueError, AttributeError):
        return []
    return [n for n in names if isinstance(n, str) and os.path.isfile(os.path.join(root, n, "character.md"))]


def _colour_map(root):
    out = {}
    for d in sorted(os.listdir(root)):
        if d.startswith((".", "__")) or not os.path.isfile(os.path.join(root, d, "character.md")):
            continue
        out[display_for(d)] = colour_of(root, d) or NEUTRAL_HEX
    return out


def _persona_list(st):
    return [{"name": display_for(n), "color": colour_of(st.char_root, n) or NEUTRAL_HEX} for n in load_personas(st.char_root)]


def _persona_for(st, user):
    # The persona this account plays as. Never chosen yet: the first persona. Chosen as none: None.
    personas = load_personas(st.char_root)
    raw = st.store.get_pref(user["id"], "persona")
    if raw is None:
        return personas[0] if personas else None
    return raw if raw in personas else None


def _persona_info(st, user):
    name = _persona_for(st, user)
    return {"name": display_for(name), "color": colour_of(st.char_root, name) or NEUTRAL_HEX} if name else None


def _avatar_shape(store, uid):
    # Squircle unless the account picked circles; any other stored value counts as squircle.
    return "circle" if store.get_pref(uid, "avatar_shape") == "circle" else "squircle"


def _has_background(root, name):
    try:
        return editor.background_path(root, name) is not None
    except editor.EditorError:
        return False


def _has_icon(root, name):
    # Icon check for listing. A folder name the editor won't accept simply has no icon here.
    try:
        return editor.icon_path(root, name) is not None
    except editor.EditorError:
        return False


def _thinking_on(st, user):
    return st.store.get_pref(user["id"], "thinking") == "1"


def _extra_models(st):
    # Provider model ids the server has set up. Their names are the only ones a user may pin.
    out = []
    client = st.client
    if isinstance(client, llm_backends.MultiClient):
        for name, c in client.providers.items():
            if c is None:
                continue
            prefix = next(p for p, n in llm_backends.PREFIXES.items() if n == name)
            out.extend(prefix + m for m in c.model_names())
    return out


def client_ip(request):
    # Behind the tunnel every request arrives from localhost, so the real address comes from Cloudflare's
    # header. It is trusted only from localhost, because anyone else could send it.
    peer = request.client.host if request.client else ""
    if peer in ("127.0.0.1", "::1"):
        forwarded = request.headers.get("cf-connecting-ip", "").strip()
        try:
            return str(ipaddress.ip_address(forwarded))
        except ValueError:
            pass
    return peer or "unknown"


def _ip_error():
    return HTTPException(423, "Too many failed sign-ins for this account from this address. An admin has to unlock it.")


def _lock_error(state):
    # The refusal for a locked name: 423 until an admin unlocks it, 429 with the time left otherwise.
    if state[0] == "permanent":
        return HTTPException(423, "This account is locked after too many failed sign-ins. An admin has to unlock it.")
    minutes = max(1, math.ceil(state[1] / 60))
    return HTTPException(429, f"Too many failed sign-ins. Try again in {minutes} minute{'' if minutes == 1 else 's'}.")


def _lock_view(row, now):
    # What the admin page shows for an account's sign-in lock.
    fails = row["fails"] if row else 0
    if row and row["permanent"]:
        return {"failed_logins": fails, "login_lock": "permanent", "locked_until": None}
    if row and row["locked_until"] > now:
        return {"failed_logins": fails, "login_lock": "timed", "locked_until": row["locked_until"]}
    return {"failed_logins": fails, "login_lock": None, "locked_until": None}


# ---- two-factor sign-in and backups: helpers

_session_code_fails = {}   # user id -> (misses, locked until); guards codes and passwords entered while signed in


def _server_key():
    try:
        return secure.server_key(os.environ.get("MISO_SECRET_KEY"))
    except secure.SecureError as err:
        raise HTTPException(503, str(err))


def _totp_aad(user_id):
    # Binds a sealed secret to its account, so one account's secret can't be moved onto another row.
    return f"totp:{user_id}".encode("ascii")


def _open_secret(user):
    if not user["totp_secret"]:
        raise HTTPException(400, "Two-factor sign-in is not set up.")
    try:
        return secure.open_sealed(_server_key(), user["totp_secret"], _totp_aad(user["id"]))
    except secure.SecureError:
        raise HTTPException(503, "The two-factor secret could not be read. An admin can reset it.")


def _session_gate(user):
    # Five misses lock the signed-in checks for 15 minutes. The sign-in itself is limited by the account lock.
    _misses, until = _session_code_fails.get(user["id"], (0, 0.0))
    if time.time() < until:
        raise HTTPException(429, "Too many wrong attempts. Wait 15 minutes and try again.")


def _session_miss(user, message):
    misses, _until = _session_code_fails.get(user["id"], (0, 0.0))
    misses += 1
    _session_code_fails[user["id"]] = (0, time.time() + 900) if misses >= 5 else (misses, 0.0)
    raise HTTPException(401, message)


def _check_session_code(user, code):
    _session_gate(user)
    ok, step = secure.check_code(_open_secret(user), str(code or ""), last_step=user["totp_step"])
    if not ok:
        _session_miss(user, "That code is wrong. Enter the one showing in your app now.")
    _session_code_fails.pop(user["id"], None)
    return step


def _twofa_state(u):
    if u["totp_enabled"] and u["totp_confirmed"]:
        return "on"
    return "setup" if u["totp_enabled"] else "off"


def _build_backup(st, kind, password):
    # A consistent database copy, the chosen files, and a manifest, all encrypted. The temp copy is removed.
    tmp = backup.make_snapshot_dir()
    try:
        snap = os.path.join(tmp, "app.db")
        backup.snapshot_db(os.path.join(st.data_dir, "app.db"), snap)
        entries, total = backup.plan(st.char_root, st.data_dir, kind, snap)
        return secure.encrypt_archive(password, entries, backup.manifest(kind, entries, total, APP_VERSION))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _set_session_cookie(resp, token, remember):
    # A remembered device keeps a persistent cookie; otherwise the cookie ends with the browser session.
    extra = {"max_age": REMEMBER_SECONDS} if remember else {}
    resp.set_cookie(COOKIE, token, httponly=True, samesite="lax", secure=COOKIE_SECURE, path="/", **extra)


def scan_characters(root):
    # Solo characters have character.md; groups have group_config.json. Anything else is ignored.
    solo, groups = [], {}
    personas = set(load_personas(root))
    for d in sorted(os.listdir(root)):
        p = os.path.join(root, d)
        if d.startswith((".", "__")) or not os.path.isdir(p) or d in personas:
            continue
        gcfg = os.path.join(p, "group_config.json")
        if os.path.exists(gcfg):
            try:
                with open(gcfg, encoding="utf-8") as f:
                    members = json.load(f).get("members", [])
            except (OSError, ValueError):
                continue
            groups[d] = [m for m in members if isinstance(m, str)]
        elif os.path.exists(os.path.join(p, "character.md")):
            solo.append(d)
    return solo, groups


@dataclass
class Slot:
    engine: object
    lock: threading.Lock


def build_client(gemini, data_dir):
    # One client that routes provider model ids (deepseek:, local:, claude:) and passes bare ids to Gemini.
    cfg = providers.load(data_dir)
    extra = {}
    if cfg["deepseek"]["enabled"] and cfg["deepseek"]["api_key"]:
        extra["deepseek"] = llm_backends.OpenAIStyleClient("DeepSeek", llm_backends.DEEPSEEK_BASE_URL,
                                                              cfg["deepseek"]["api_key"], "deepseek")
    if cfg["local"]["enabled"] and cfg["local"]["base_url"]:
        extra["local"] = llm_backends.OpenAIStyleClient("local model", cfg["local"]["base_url"],
                                                           cfg["local"]["api_key"], "local")
    if cfg["claude"]["enabled"]:
        exe = cfg["claude"]["exe"] or shutil.which("claude")
        if exe:
            empty = os.path.join(data_dir, "claude-workdir")
            os.makedirs(empty, exist_ok=True)
            extra["claude"] = llm_backends.ClaudeCodeClient(exe, empty)
    return llm_backends.MultiClient(gemini, extra)


class AppState:
    def __init__(self, store, client, data_dir, char_root):
        self.store = store
        self.client = client
        self.data_dir = data_dir
        self.char_root = char_root
        self.slots = {}                       # (user_id, profile) -> Slot
        self.slots_lock = threading.Lock()
        self.rpm = collections.deque()        # timestamps of recent Gemini calls
        self.rpm_lock = threading.Lock()


def S(request):
    return request.app.state.rp


def can_edit_card(user, char_path):
    # Creators edit their own characters. A character made as 'admin' (the default for older folders) is edited by any admin.
    creator = editor.creator_of(char_path)
    return creator == user["username"] or (creator == editor.DEFAULT_CREATOR and bool(user["is_admin"]))


def _csrf(request):
    # Browsers won't send a custom header cross-site without a CORS preflight, which we never allow.
    if request.headers.get("x-requested-with") != "roleplay":
        raise HTTPException(403, "Bad request.")


def _current_user(request):
    return S(request).store.session_user(request.cookies.get(COOKIE))


def require(request, allow_change=False, admin=False):
    user = _current_user(request)
    if user is None:
        raise HTTPException(401, "Please log in.")
    if user["must_change"] and not allow_change:
        raise HTTPException(403, {"code": "password_change_required",
                                  "message": "Please change your password first."})
    if admin and not user["is_admin"]:
        raise HTTPException(403, "Admins only.")
    return user


def _effective_cap(st, user):
    if user["daily_cap"] is not None:
        return int(user["daily_cap"])
    return int(st.store.get_setting("user_daily_cap"))


def _usage_summary(st, user):
    used = st.store.usage_today(user["id"])
    return {"requests_today": used["requests"], "request_cap": _effective_cap(st, user),
            "tokens_today": used["tokens"], "token_display_limit": TOKEN_DISPLAY_LIMIT}


BUDGET_KINDS = (
    ("daily", "token_daily", "resets at midnight Pacific"),
    ("weekly", "token_weekly", "resets on Monday at midnight Pacific"),
    ("monthly", "token_monthly", "resets on the 1st at midnight Pacific"),
)


def _budget_status(st, user):
    # For each window: the admin's token budget (None = no budget), tokens used in it, and when it resets.
    out = []
    for kind, column, reset in BUDGET_KINDS:
        budget = user.get(column)
        used = st.store.usage_tokens(user["id"], budget_days(kind)) if budget is not None else 0
        out.append({"kind": kind, "budget": budget, "used": used, "reset": reset})
    return out


def _budget_block(st, user):
    # Why a turn must wait, or None: a window whose tokens are already at or over its budget blocks.
    for b in _budget_status(st, user):
        if b["budget"] is not None and b["used"] >= b["budget"]:
            return (f"Your {b['kind']} token budget is used up ({b['used']:,} of {b['budget']:,} tokens). "
                    f"It {b['reset']}.")
    return None


def _count_model_call(st, uid, model):
    # One model call made for this user. Counts toward their usage and the per-minute limit.
    st.store.add_usage(uid, requests=1, provider=provider_of(model))
    with st.rpm_lock:
        st.rpm.append(time.time())


def _card_for(st, folder):
    # What the home page shows for a character. Never the character.md text.
    prof = profiles.read_profile(os.path.join(st.char_root, folder))
    name = display_for(folder)
    return {"name": name, "headline": prof["headline"] if prof else "", "info": prof["info"] if prof else "",
            "tags": prof["tags"] if prof else [], "has_profile": prof is not None,
            "pending": folder in st.profiles.pending(), "has_icon": _has_icon(st.char_root, name),
            "has_background": _has_background(st.char_root, name),
            "color": colour_of(st.char_root, folder) or NEUTRAL_HEX,
            "creator": editor.creator_of(os.path.join(st.char_root, folder))}


def _known_tags(st):
    # Tags on solo characters, most used first. Passed to the writer so it reuses them.
    solo, _ = scan_characters(st.char_root)
    return [t["name"] for t in profiles.tag_counts(st.char_root, solo)]


def _queue_missing_profiles(st, user, folders):
    # Ask the background writer for characters that have no profile yet. Skipped when this user is over a cap.
    missing = [f for f in folders if profiles.read_profile(os.path.join(st.char_root, f)) is None]
    if not missing:
        return
    try:
        _check_caps(st, user)
    except HTTPException:
        return
    st.profiles.want(missing, user["id"])


def _check_caps(st, user):
    # Pre-turn check. Group turns can overshoot a little: each member is one call.
    used = st.store.usage_today(user["id"])["requests"]
    cap = _effective_cap(st, user)
    if used >= cap:
        raise HTTPException(429, {"code": "cap_reached",
                                  "message": f"Daily request cap reached ({used}/{cap}). "
                                             "It resets at midnight Pacific."})
    blocked = _budget_block(st, user)
    if blocked:
        raise HTTPException(429, {"code": "budget_reached", "message": blocked})
    if st.store.global_requests_today() >= int(st.store.get_setting("global_daily_cap")):
        raise HTTPException(429, {"code": "global_cap",
                                  "message": "The server's daily budget is used up for today."})
    limit = int(st.store.get_setting("rpm_limit"))
    now = time.time()
    with st.rpm_lock:
        while st.rpm and now - st.rpm[0] > 60:
            st.rpm.popleft()
        busy = len(st.rpm) >= limit
    if busy:
        raise HTTPException(429, {"code": "busy",
                                  "message": "Too many requests right now. Wait a few seconds."})


def _admin_sandbox_root(st, name, uid):
    # The sandbox folder one account has for one solo character. Admin views use this; nothing is created.
    folder, is_group = _resolve_profile(st, name)
    if is_group:
        raise HTTPException(404, "Groups have no sandbox.")
    if st.store.get_user(uid) is None:
        raise HTTPException(404, "Unknown account.")
    return sandbox.root_for(os.path.join(st.data_dir, "users", str(uid), folder))


def _resolve_profile(st, name, user=None):
    # Client names are display names; the folder name is the ID used on disk and in the engine.
    # An account's own group is matched by its name and resolves to its group ID (grp-...).
    name = folder_for(str(name))
    solo, groups = scan_characters(st.char_root)
    if name in groups:
        return name, True
    if name in solo:
        return name, False
    if user is not None:
        own = private_groups.find_by_key(st.data_dir, user["id"], name, folder_for)
        if own is not None:
            return own["id"], True
    raise HTTPException(404, "Unknown character.")


def _label(st, user, name):
    # The name a person sees: a private group's own name, else the character's display name.
    if name.startswith(private_groups.PREFIX):
        item = private_groups.get(st.data_dir, user["id"], name)
        if item is not None:
            return item["name"]
    return display_for(name)


SWITCHES = ("memory_enabled", "palace_enabled")


def _apply_switches(st, engine):
    # Read per request, so an admin change takes effect on the next message.
    engine.memory_enabled = st.store.get_setting("memory_enabled") == "1"
    engine.palace_enabled = st.store.get_setting("palace_enabled") == "1"


def _slot(st, user, name, is_group):
    persona = _persona_for(st, user)
    thinking = _thinking_on(st, user)
    key = (user["id"], name, persona, thinking)
    with st.slots_lock:
        slot = st.slots.get(key)
    if slot is not None:
        _apply_switches(st, slot.engine)
        return slot
    uid = user["id"]
    state_root = os.path.join(st.data_dir, "users", str(uid), name)
    # A branch chat loads from its own folder; the main chat is the account folder itself.
    state_dir = branches.active_dir(state_root)
    os.makedirs(state_dir, exist_ok=True)

    def on_request(model=None):
        st.store.add_usage(uid, requests=1, provider=provider_of(model))
        with st.rpm_lock:
            st.rpm.append(time.time())

    def on_tokens(n, model=None):
        st.store.add_usage(uid, tokens=n, provider=provider_of(model))
        return (st.store.usage_today(uid)["tokens"], TOKEN_DISPLAY_LIMIT)

    engine = Engine(st.client, name, persona, log=lambda m: None, state_dir=state_dir,
                    on_request=on_request, on_tokens=on_tokens, allow_root_user=False,
                    member_memory=False, persist_config=False)
    _apply_switches(st, engine)
    if not is_group:
        # Each account has its own palace per character; the first chat copies the starting one.
        engine.palace_dir = os.path.join(st.data_dir, "users", str(uid), name)
        palace.copy_starting(os.path.join(st.char_root, name), engine.palace_dir)
    engine.extra_models = _extra_models(st)
    engine.discover_models()
    engine.thinking = thinking
    engine.window_override = _pref_int(st.store.get_pref(uid, "window"))
    engine.temperature_override = _pref_float(st.store.get_pref(uid, "temp:" + name))
    engine.reply_length_override = _reply_target(st, uid)
    chosen = st.store.get_pref(user["id"], "model") or ""
    if chosen and (chosen in engine.fallback_models or chosen in engine.extra_models):
        engine.pin_model(chosen)   # a saved model choice applies to every chat until changed
    engine.load_profile()
    if name.startswith(private_groups.PREFIX):
        cfg = private_groups.get(st.data_dir, uid, name)
        if cfg is None:
            raise HTTPException(404, "Unknown group.")
        engine.apply_private_group(cfg["name"], cfg["members"], private_groups.get_notes(st.data_dir, uid, name))
    slot = Slot(engine, threading.Lock())
    with st.slots_lock:
        return st.slots.setdefault(key, slot)


def _drop_slot(st, user, name):
    # After a failed or interrupted turn, reload from disk so memory never holds half a turn.
    with st.slots_lock:
        for k in [k for k in st.slots if k[0] == user["id"] and k[1] == name]:
            st.slots.pop(k, None)


PROVIDER_KEYS = ["gemini", "deepseek", "local", "claude"]   # each provider uses its own key


def provider_of(model):
    # Which provider a model call used, from the model name's prefix (deepseek:, local:, claude:); else Gemini.
    for prefix, name in llm_backends.PREFIXES.items():
        if model and str(model).startswith(prefix):
            return name
    return "gemini"


def _character_stats(st):
    # Totals per character and per group across every account, from each account's saved history and memory
    # bank. The terminal's own history in the shared character folders is not included.
    chars, groups = {}, {}
    users_root = os.path.join(st.data_dir, "users")
    if not os.path.isdir(users_root):
        return [], []
    for uid in sorted(os.listdir(users_root)):
        udir = os.path.join(users_root, uid)
        if not os.path.isdir(udir):
            continue
        for name in os.listdir(udir):
            cdir = os.path.join(udir, name)
            base = os.path.join(st.char_root, name)
            if not os.path.isdir(cdir) or not os.path.isdir(base):
                continue
            is_group = os.path.exists(os.path.join(base, "group_config.json"))
            if not is_group and not os.path.exists(os.path.join(base, "character.md")):
                continue
            bucket = groups if is_group else chars
            row = bucket.setdefault(name, {"name": name, "accounts": 0, "messages": 0, "history_tokens": 0,
                                           "memory_tokens": 0, "last_memory": 0.0, "breakdown": {}})
            row["accounts"] += 1
            hist_path = os.path.join(cdir, "memory.json")
            if os.path.exists(hist_path):
                try:
                    with open(hist_path, encoding="utf-8") as f:
                        history = json.load(f)
                except (OSError, ValueError):
                    history = []
                for e in history if isinstance(history, list) else []:
                    if not isinstance(e, dict):
                        continue
                    toks = Engine.estimate_tokens(str(e.get("text") or ""))
                    row["messages"] += 1
                    row["history_tokens"] += toks
                    if is_group:
                        who = str(e.get("speaker") or e.get("role") or "unknown")
                        part = row["breakdown"].setdefault(who, {"messages": 0, "tokens": 0})
                        part["messages"] += 1
                        part["tokens"] += toks
            mem_path = os.path.join(cdir, "memory.md")
            if os.path.exists(mem_path):
                try:
                    with open(mem_path, encoding="utf-8", errors="replace") as f:
                        row["memory_tokens"] += Engine.estimate_tokens(f.read())
                    row["last_memory"] = max(row["last_memory"], os.path.getmtime(mem_path))
                except OSError:
                    pass

    def stamp(ts):
        return time.strftime("%d/%m/%y %H:%M", time.localtime(ts)) if ts else "N/A"

    out_chars = [{"name": display_for(r["name"]), "accounts": r["accounts"], "messages": r["messages"],
                  "history_tokens": r["history_tokens"], "memory_tokens": r["memory_tokens"],
                  "last_memory": stamp(r["last_memory"])} for r in chars.values()]
    out_groups = []
    for r in groups.values():
        members = []
        try:
            with open(os.path.join(st.char_root, r["name"], "group_config.json"), encoding="utf-8") as f:
                members = json.load(f).get("members", [])
        except (OSError, ValueError, AttributeError):
            pass
        breakdown = [{"speaker": display_for(m), **r["breakdown"].get(m, {"messages": 0, "tokens": 0})} for m in members]
        breakdown += [{"speaker": display_for(w), **p} for w, p in r["breakdown"].items() if w not in members]
        out_groups.append({"name": display_for(r["name"]), "accounts": r["accounts"], "members": [display_for(m) for m in members],
                           "messages": r["messages"], "history_tokens": r["history_tokens"],
                           "memory_tokens": r["memory_tokens"], "last_memory": stamp(r["last_memory"]),
                           "breakdown": breakdown})
    out_chars.sort(key=lambda r: -r["messages"])
    out_groups.sort(key=lambda r: -r["messages"])
    return out_chars, out_groups


def _history(engine):
    # Speaker labels match the live view: 'you', the character (solo), or the member name (group).
    # Stored reasoning is returned too, so a reload shows the same thinking panel as the live turn.
    out = []
    for e in engine.full_history:
        role = e.get("role")
        if e.get("speaker") == "narrator":
            speaker = "Narrator"
        elif role == "user":
            speaker = engine.user_display_name
        else:
            speaker = display_for(e["speaker"]) if e.get("speaker") else engine.character_name
        item = {"speaker": speaker, "role": role, "text": engine.restore(e.get("text", ""))}
        if e.get("thinking"):
            item["thinking"] = e["thinking"]
        if e.get("role") == "user" and e.get("speaker") != "narrator":
            variants = e.get("variants") or []
            item["variant"] = {"index": int(e.get("variant", len(variants) - 1)) + 1 if variants else 1,
                               "count": max(len(variants), 1), "limit": MAX_REGENERATIONS}
        if e.get("ts"):
            item["time"] = e["ts"]
        out.append(item)
    return out


def _read_body(body):
    if not isinstance(body, dict):
        raise HTTPException(400, "Expected a JSON object.")
    return body


SSE_HEADERS = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}
_END = object()


def _sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _undo_now(st, user, name, is_group):
    slot = _slot(st, user, name, is_group)
    with slot.lock:
        if not slot.engine.undo():
            raise HTTPException(400, "Nothing to undo.")
        return {"messages": _history(slot.engine)}


def _swipe_now(st, user, name, is_group, delta):
    slot = _slot(st, user, name, is_group)
    with slot.lock:
        if not slot.engine.swipe(delta):
            raise HTTPException(400, "No other reply to show.")
        return {"messages": _history(slot.engine)}


def _memory_files(st, user, name):
    # This account's memory bank and pinned facts, in the folder of the branch that is open.
    state_dir = branches.active_dir(os.path.join(st.data_dir, "users", str(user["id"]), name))
    return os.path.join(state_dir, "memory.md"), os.path.join(state_dir, "pinned.md")


def _read_or_empty(path):
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def _write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def _summary_path(st, user, name):
    return os.path.join(branches.active_dir(os.path.join(st.data_dir, "users", str(user["id"]), name)), "summary.json")


def _read_summary(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return ""
    return str(data.get("text") or "") if isinstance(data, dict) else ""


def _write_summary(path, text):
    # Edit the summary text only; keep the count of messages it covers.
    covered = 0
    try:
        with open(path, encoding="utf-8") as f:
            covered = max(0, int(json.load(f).get("covered", 0)))
    except (OSError, ValueError, TypeError, AttributeError):
        covered = 0
    _write_text(path, json.dumps({"text": text, "covered": covered}, ensure_ascii=False, indent=2))


def _pref_int(value):
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _pref_float(value):
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _reply_bounds(store):
    # The admin's site-wide range for the reply length target, in tokens.
    return int(store.get_setting("reply_min_tokens")), int(store.get_setting("reply_max_tokens"))


def _reply_target(st, uid):
    # This account's reply length target, kept inside the admin's range. None = no target.
    raw = _pref_int(st.store.get_pref(uid, "reply_length"))
    if raw is None:
        return None
    lo, hi = _reply_bounds(st.store)
    return max(lo, min(hi, raw))


def _chat_settings_payload(st, user, name, is_group):
    return {"character": _label(st, user, name), "is_group": bool(is_group),
            "temperature": _pref_float(st.store.get_pref(user["id"], "temp:" + name)),
            "temperature_default": GROUP_TEMPERATURE if is_group else SOLO_TEMPERATURE,
            "window": _pref_int(st.store.get_pref(user["id"], "window")),
            "window_default": GROUP_WINDOW if is_group else SOLO_WINDOW,
            "window_defaults": {"solo": SOLO_WINDOW, "group": GROUP_WINDOW}}


def _export_text(messages, title, fmt, when):
    if fmt == "md":
        lines = [f"# {title}", "", f"Exported {when}", ""]
        for m in messages:
            stamp = f" - {m['time']}" if m.get("time") else ""
            lines += [f"**{m['speaker']}**{stamp}", "", m["text"], ""]
    else:
        lines = [title, f"Exported {when}", ""]
        for m in messages:
            stamp = f" ({m['time']})" if m.get("time") else ""
            lines += [f"{m['speaker']}{stamp}:", m["text"], ""]
    return "\n".join(lines) + "\n"


def _export_now(st, user, name, is_group, fmt):
    slot = _slot(st, user, name, is_group)
    with slot.lock:
        messages = _history(slot.engine)
    return _export_text(messages, _label(st, user, name), fmt, time.strftime("%Y-%m-%d %H:%M"))


def _run_turn(st, user, name, slot, message, regen=False):
    # Run one turn on a worker thread and yield SSE events.
    #
    # Gemini calls block, so they never run on the event loop. Events, in order:
    # delta* (streamed text, speaker + text so far), then done (final replies) or error,
    # then usage, then the stream ends. The turn is saved on the server even if the
    # browser disconnects mid-stream.
    events = queue.Queue()

    def on_delta(speaker, text):
        events.put(("delta", {"speaker": speaker, "text": slot.engine.restore(text)}))

    def on_think(speaker, text):
        events.put(("thinking", {"speaker": speaker, "text": text}))

    def worker():
        try:
            with slot.lock:
                # re-check under the lock: two requests can pass the pre-check together
                cap = _effective_cap(st, user)
                if st.store.usage_today(user["id"])["requests"] >= cap:
                    events.put(("error", {"code": "cap_reached",
                                          "message": f"Daily request cap reached ({cap}). "
                                                     "It resets at midnight Pacific."}))
                    return
                blocked = _budget_block(st, user)   # re-checked under the lock, like the request cap
                if blocked:
                    events.put(("error", {"code": "budget_reached", "message": blocked}))
                    return
                if regen and slot.engine.regen_count() >= MAX_REGENERATIONS:
                    events.put(("error", {"code": "regen_limit",
                                          "message": f"This message already has {MAX_REGENERATIONS} regenerations, "
                                                       "the most allowed. Send a new message to carry on."}))
                    return
                if regen:
                    text = slot.engine.begin_regenerate()
                    if text is None:
                        events.put(("error", {"code": "nothing", "message": "There is no reply to regenerate yet."}))
                        return
                try:
                    started = time.time()
                    processed = text if regen else slot.engine.prepare_input(message)
                    result = slot.engine.send(processed, on_delta=on_delta, on_think=on_think)
                    if regen and result.ok:
                        slot.engine.finish_regenerate()
                    model_used = result.replies[-1].model if result.replies else slot.engine.model_name
                    log(f"turn {name} for user {user['id']}: {time.time() - started:.1f}s, model {model_used}, ok {result.ok}")
                except Exception as err:
                    log(f"turn failed for user {user['id']} on {name}: {err}")
                    _drop_slot(st, user, name)
                    events.put(("error", {"code": "server",
                                          "message": "Something broke on the server. Your message was not saved."}))
                    return
                if not result.ok:
                    _drop_slot(st, user, name)
                    detail = (result.error or "no answer")[:200]
                    events.put(("error", {"code": "model_failed", "message": f"No model answered: {detail}"}))
                    return
                if result.replies:   # the user's message and each reply, for the stats page
                    st.store.add_usage(user["id"], messages=(0 if regen else 1) + len(result.replies),
                                       provider=provider_of(result.replies[-1].model))
                events.put(("done", {
                    "replies": [{"speaker": r.speaker, "color": r.color, "text": r.display, "model": r.model,
                                 "thinking": r.thinking}
                                for r in result.replies],
                    "selected": result.selected,
                    "selector_note": result.selector_note,
                }))
                if result.memory_due and st.store.usage_today(user["id"])["requests"] < _effective_cap(st, user):
                    try:
                        slot.engine.update_memory_bank()
                    except Exception as err:  # memory refresh is best-effort
                        log(f"memory refresh failed for {name}: {err}")
                events.put(("usage", _usage_summary(st, user)))
        finally:
            events.put(_END)

    threading.Thread(target=worker, daemon=True).start()
    while True:
        item = events.get()
        if item is _END:
            return
        yield _sse(*item)


COMMAND_WORDS = {"/help", "/undo", "/back", "/model", "/save", "/verbose", "/exit", "/quit", "/quit-clear"}
COMMAND_HELP = (
    "**Commands**\n\n"
    "- `/undo` or `/back`: remove the last exchange\n"
    "- `/model`: show the current model and any pin\n"
    "- `/model <name>`: pin a model for this chat\n"
    "- `/model auto`: go back to the automatic fallback chain\n"
    "- `/save`: refresh this character's memory.md now\n"
    "- `/help`: this list\n\n"
    "`/verbose` only works in the terminal. Log out to leave; `/exit` and `/quit` do the same thing."
)


def _parse_command(message):
    # (word, arg) for a known command, or None. Unknown slash words go to the model as chat, as in the terminal.
    text = message.strip()
    if text.lower() in ("exit", "quit"):
        return "/" + text.lower(), ""
    if not text.startswith("/"):
        return None
    parts = text.split(None, 1)
    word = parts[0].lower()
    if word not in COMMAND_WORDS:
        return None
    return word, (parts[1].strip() if len(parts) > 1 else "")


def _run_command(st, user, name, is_group, word, arg):
    # Run a command against this user's engine. Commands other than /save make no model call.
    if word == "/help":
        return {"notice": COMMAND_HELP}
    if word in ("/exit", "/quit", "/quit-clear"):
        return {"notice": "Log out when you're done. Your chat is saved after every reply."}
    if word == "/verbose":
        return {"notice": "Verbose prompt diagnostics are terminal-only."}
    slot = _slot(st, user, name, is_group)
    with slot.lock:
        eng = slot.engine
        if word == "/model":
            current = getattr(eng, "model_name", None) or "none yet"
            pinned = eng.pinned_model or "none (automatic fallback chain)"
            if not arg:
                return {"notice": f"Current model: {current}. Pinned: {pinned}.\n\nUsage: `/model <name>` or `/model auto`."}
            if arg.lower() in ("auto", "default", "reset", "off"):
                eng.clear_pin()
                return {"notice": "Model pin cleared. Using the automatic fallback chain."}
            chosen = eng.resolve_model_name(arg)
            if isinstance(chosen, list):
                return {"notice": f"'{arg}' matches more than one model: {', '.join(chosen)}. Be more specific."}
            if not chosen:
                return {"notice": "No model name given."}
            eng.pin_model(chosen)
            note = "" if chosen in eng.fallback_models else " It isn't in the discovered list, so the fallback chain takes over if it fails."
            return {"notice": f"Model pinned for this chat: {chosen}.{note}"}
        if word in ("/undo", "/back"):
            if eng.undo():
                return {"notice": "Undid the last exchange.", "reload": True}
            return {"notice": "Nothing to undo."}
        if word == "/save":
            if not eng.full_history:
                return {"notice": "No history to save yet."}
            if st.store.usage_today(user["id"])["requests"] >= _effective_cap(st, user):
                return {"notice": "Daily request cap reached, so memory.md was not refreshed."}
            eng.update_memory_bank()
            _drop_slot(st, user, name)   # the next reply reloads the new memory.md
            return {"notice": "memory.md refreshed."}
    return {"notice": "Nothing to do."}


def make_app(client=None, data_dir=None, char_root=None):
    char_root = char_root or CHAR_ROOT
    data_dir = data_dir or DATA_DIR
    os.chdir(char_root)  # engine paths (character folders, group configs) are relative
    if client is None:
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        client = genai.Client(api_key=key)

    store = Store(os.path.join(data_dir, "app.db"))
    store.ensure_admin()
    st = AppState(store, build_client(client, data_dir), data_dir, char_root)
    st.data_dir_ref = data_dir
    st.profile_gate = profiles.RateGate(profiles.PROFILE_RPM, reserve=profiles.PROFILE_RESERVE)   # shared by the background writer and Auto-fill
    st.profiles = profiles.ProfileWriter(char_root, st.client, gate=st.profile_gate, log=lambda m: print(m, flush=True))
    st.profiles.on_call = lambda uid, model: _count_model_call(st, uid, model)
    st.profiles.tag_source = lambda: _known_tags(st)

    app = FastAPI(title="Miso Chat", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.rp = st
    app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers["Content-Security-Policy"] = CSP
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "same-origin"
        if request.url.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        elif request.url.path == "/" or request.url.path.startswith(("/static/", "/login", "/admin", "/2fa-setup")):
            # pages and assets must revalidate, so an old cached copy never hides a new layout
            resp.headers["Cache-Control"] = "no-cache"
        return resp

    def page(name):
        return FileResponse(os.path.join(HERE, "static", name), media_type="text/html")

    @app.get("/")
    def index():
        return page("home.html")   # the character gallery

    @app.get("/chat")
    def chat_page():
        return page("app.html")

    @app.get("/login")
    def login_page():
        return page("login.html")

    @app.get("/2fa-setup")
    def twofa_setup_page():
        return page("twofa_setup.html")

    @app.get("/admin")
    def admin_page():
        return page("admin.html")

    @app.get("/admin/stats")
    def admin_stats_page():
        return page("stats.html")

    @app.get("/admin/sandboxes")
    def admin_sandboxes_page():
        return page("sandboxes.html")

    @app.get("/api/admin/sandboxes/{name}")
    def admin_sandbox_accounts(name: str, request: Request):
        # Every account's sandbox for one character: files and bytes held. Admin only.
        require(request, admin=True)
        folder, is_group = _resolve_profile(st, name)
        if is_group:
            raise HTTPException(404, "Groups have no sandbox.")
        users = []
        for u in st.store.list_users():
            root = sandbox.root_for(os.path.join(st.data_dir, "users", str(u["id"]), folder))
            files, size = sandbox.usage(root)
            users.append({"id": u["id"], "username": u["username"], "files": files, "bytes": size})
        return {"character": display_for(folder),
                "enabled": sandbox.is_enabled(os.path.join(st.char_root, folder)), "users": users}

    @app.get("/api/admin/sandboxes/{name}/{uid}/list")
    def admin_sandbox_list(name: str, uid: int, request: Request, path: str = "/"):
        require(request, admin=True)
        root = _admin_sandbox_root(st, name, uid)
        if not os.path.isdir(root):
            return {"path": "/", "entries": [], "truncated": False}
        try:
            return sandbox.list_dir(root, path)
        except sandbox.SandboxError as err:
            raise HTTPException(400, str(err))

    @app.get("/api/admin/sandboxes/{name}/{uid}/file")
    def admin_sandbox_file(name: str, uid: int, request: Request, path: str = "/"):
        require(request, admin=True)
        root = _admin_sandbox_root(st, name, uid)
        if not os.path.isdir(root):
            raise HTTPException(404, "This account has no sandbox for this character yet.")
        try:
            return sandbox.read_file(root, path)
        except sandbox.SandboxError as err:
            raise HTTPException(400, str(err))

    @app.get("/api/admin/stats")
    def admin_stats(request: Request, days: int = 30, provider: str = "all"):
        # Usage over time (per day, optionally one provider's key), today's usage, and the character tables.
        require(request, admin=True)
        days = min(max(int(days), 1), 365)
        if provider != "all" and provider not in PROVIDER_KEYS:
            provider = "all"
        day_list = recent_days(days)
        by_day = {r["day"]: r for r in st.store.usage_series(day_list, provider)}
        series = []
        for d in day_list:
            r = by_day.get(d, {})
            series.append({"day": d, "requests": int(r.get("requests") or 0),
                           "tokens": int(r.get("tokens") or 0), "messages": int(r.get("messages") or 0)})
        totals = {p: {"requests": 0, "tokens": 0, "messages": 0} for p in PROVIDER_KEYS}
        for r in st.store.usage_by_provider(day_list):
            totals[r["provider"]] = {"requests": int(r["requests"] or 0), "tokens": int(r["tokens"] or 0),
                                     "messages": int(r["messages"] or 0)}
        today = day_list[-1]
        characters, groups = _character_stats(st)
        return {
            "days": days, "provider": provider, "providers": PROVIDER_KEYS,
            "series": series, "totals": totals,
            "today": {"day": today, "global_requests": int(st.store.global_requests_today()),
                      "global_cap": int(st.store.get_setting("global_daily_cap")),
                      "users": st.store.usage_users_on(today, provider)},
            "characters": characters, "groups": groups,
            "note": "Per-provider counts start when this update went live. Earlier usage is counted as Gemini, "
                    "with no message counts.",
        }

    # ---- session

    @app.post("/api/login")
    async def login(request: Request):
        _csrf(request)
        body = _read_body(await request.json())
        username = str(body.get("username", "")).strip()
        password = str(body.get("password", ""))
        remember = bool(body.get("remember", True))
        # A locked name is refused before its password is checked, so the lock can't be guessed through.
        blocked = store.login_block(username)
        if blocked:
            raise _lock_error(blocked)
        ip = client_ip(request)
        if store.ip_blocked(username, ip):
            raise _ip_error()
        user = await run_in_threadpool(store.authenticate, username, password)
        if user is None:
            # Both counters move on every wrong guess, even for names that don't exist.
            ip_locked = store.record_failed_ip(username, ip)
            lock = store.record_failed_login(username)
            if lock:
                if lock[0] == "permanent":
                    found = store.find_user(username)
                    if found:
                        store.end_all_sessions(found["id"])
                raise _lock_error(lock)
            if ip_locked:
                raise _ip_error()
            raise HTTPException(401, "Wrong username or password.")
        if not user["totp_enabled"]:
            # Counters clear only once a sign-in is complete. With two-factor on, a correct password
            # is half of it, so the code step clears them when the code is right.
            store.clear_login_lock(username)
            store.clear_ip_throttle(username, ip)
        if needs_rehash(user["pw_hash"]):
            # Hashes made at a lower scrypt cost are upgraded now that the password is known.
            store.upgrade_hash(user["id"], password)
        if user["totp_enabled"]:
            # Two-factor sign-in: the password only opens a short pending session that finishes with a code.
            key = _server_key()
            if not user["totp_confirmed"]:
                # First sign-in with two-factor on: a new secret to set up the app. The password is enough to see it.
                secret = secure.new_totp_secret()
                store.set_totp(user["id"], secure.seal(key, secret, _totp_aad(user["id"])), True, False)
                token = store.new_pending_session(user["id"], "setup", remember)
                resp = JSONResponse({"setup_2fa": True, "secret": secret,
                                     "qr": secure.qr_svg(secure.provisioning_uri(secret, user["username"]))})
            else:
                token = store.new_pending_session(user["id"], "otp", remember)
                resp = JSONResponse({"otp_required": True})
            _set_session_cookie(resp, token, remember)
            return resp
        token = store.new_session(user["id"], remember=remember)
        resp = JSONResponse({"must_change": bool(user["must_change"]), "is_admin": bool(user["is_admin"])})
        _set_session_cookie(resp, token, remember)
        return resp

    @app.get("/api/login/2fa/setup")
    def login_2fa_setup(request: Request):
        # The set-up page reads its QR code and key again on each load. Only a pending first set-up sees them.
        user = store.pending_user(request.cookies.get(COOKIE), "setup")
        if user is None:
            raise HTTPException(401, "Sign in again.")
        secret = _open_secret(user)
        return {"secret": secret, "qr": secure.qr_svg(secure.provisioning_uri(secret, user["username"]))}

    @app.post("/api/login/2fa")
    async def login_2fa(request: Request):
        # Finishes a sign-in that needed a code: the app's code, or the first code after set-up.
        _csrf(request)
        body = _read_body(await request.json())
        token = request.cookies.get(COOKIE)
        stage, user = "otp", store.pending_user(token, "otp")
        if user is None:
            stage, user = "setup", store.pending_user(token, "setup")
        if user is None:
            raise HTTPException(401, "Sign in again.")
        blocked = store.login_block(user["username"])
        if blocked:
            raise _lock_error(blocked)
        ok, step = secure.check_code(_open_secret(user), str(body.get("code", "")), last_step=user["totp_step"])
        if not ok:
            lock = store.record_failed_login(user["username"])
            if lock:
                if lock[0] == "permanent":
                    store.end_all_sessions(user["id"])
                raise _lock_error(lock)
            raise HTTPException(401, "That code is wrong or was already used. Enter the one showing now.")
        store.clear_login_lock(user["username"])
        store.clear_ip_throttle(user["username"], client_ip(request))
        if stage == "setup":
            store.set_totp(user["id"], user["totp_secret"], True, True, step)
        else:
            store.set_totp_step(user["id"], step)
        store.promote_session(token, bool(user["remember"]))
        resp = JSONResponse({"must_change": bool(user["must_change"]), "is_admin": bool(user["is_admin"])})
        _set_session_cookie(resp, token, bool(user["remember"]))
        return resp

    @app.post("/api/logout")
    def logout(request: Request):
        _csrf(request)
        store.end_session(request.cookies.get(COOKIE))
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE, path="/")
        return resp

    @app.get("/api/me")
    def me(request: Request):
        user = require(request, allow_change=True)
        info = {"username": user["username"], "is_admin": bool(user["is_admin"]),
                "must_change": bool(user["must_change"])}
        info["features"] = {"memory": st.store.get_setting("memory_enabled") == "1",
                            "palace": st.store.get_setting("palace_enabled") == "1"}
        if not user["must_change"]:
            info["usage"] = _usage_summary(st, user)
            info["global"] = {"requests_today": store.global_requests_today(),
                              "cap": int(store.get_setting("global_daily_cap"))}
            info["persona"] = _persona_info(st, user)
            info["thinking"] = _thinking_on(st, user)
            info["reply_length"] = _reply_target(st, user["id"])
            info["reply_length_bounds"] = list(_reply_bounds(store))
            info["avatar_shape"] = _avatar_shape(store, user["id"])
            info["models"] = {"gemini": "auto", "extra": _extra_models(st)}
            info["model"] = store.get_pref(user["id"], "model") or ""   # the saved choice, so the picker reloads it
            info["personas"] = _persona_list(st)
        resp = JSONResponse(info)
        token = request.cookies.get(COOKIE)
        if store.session_remembered(token):
            _set_session_cookie(resp, token, True)   # refreshes the persistent cookie as the session slides
        return resp

    @app.post("/api/password")
    async def change_password(request: Request):
        _csrf(request)
        user = require(request, allow_change=True)
        body = _read_body(await request.json())
        old, new = str(body.get("old", "")), str(body.get("new", ""))
        if not verify_password(old, user["pw_hash"]):
            raise HTTPException(400, "The current password is wrong.")
        if len(new) < MIN_PASSWORD:
            raise HTTPException(400, f"New password must be at least {MIN_PASSWORD} characters.")
        if new == old:
            raise HTTPException(400, "Pick a different password.")
        store.set_password(user["id"], new, must_change=False)
        remember = store.session_remembered(request.cookies.get(COOKIE))
        store.end_all_sessions(user["id"])
        token = store.new_session(user["id"], remember=remember)
        resp = JSONResponse({"ok": True})
        _set_session_cookie(resp, token, remember)
        return resp

    # ---- characters and chat

    @app.get("/api/characters")
    def characters(request: Request):
        user = require(request)
        solo, groups = scan_characters(st.char_root)
        _queue_missing_profiles(st, user, solo)
        # The search order is the same for every list on this page; sidebar holds only characters this account has chatted with.
        ranked = recommend.order(st.char_root, st.data_dir, user["id"], solo)
        chatted = set(recommend.chatted(st.data_dir, user["id"], solo))
        return {"solo": [display_for(s) for s in solo], "groups": [{"name": display_for(g), "members": [display_for(x) for x in m]} for g, m in groups.items()]
                + [{"name": i["name"], "members": [display_for(x) for x in i["members"]], "id": i["id"], "private": True}
                   for i in private_groups.list_for(st.data_dir, user["id"])],
                "cards": [_card_for(st, s) for s in ranked],
                 "sidebar": [display_for(s) for s in ranked if s in chatted],
                "personas": _persona_list(st), "colors": _colour_map(st.char_root),
                "icons": [display_for(n) for n in solo + list(groups) + load_personas(st.char_root) if _has_icon(st.char_root, display_for(n))],
                "backgrounds": [display_for(n) for n in solo if _has_background(st.char_root, display_for(n))],
                "editable": [display_for(s) for s in solo if can_edit_card(user, os.path.join(st.char_root, s))],
                "recommended": [{"name": display_for(r["folder"]), "tags": r["tags"]} for r in
                                recommend.recommend(st.char_root, st.data_dir, user["id"], solo)]}

    def _group_context(user, exclude=None):
        # Names a group must not reuse, and the characters it may hold.
        solo, shared = scan_characters(st.char_root)
        own = [i for i in private_groups.list_for(st.data_dir, user["id"]) if i["id"] != exclude]
        taken = {folder_for(x) for x in solo + list(shared)} | {folder_for(i["name"]) for i in own}
        return solo, taken

    def _group_body(body):
        members = body.get("members")
        if not isinstance(members, list):
            raise HTTPException(400, "Pick the characters for the group.")
        return str(body.get("name", "")), [folder_for(str(m)) for m in members]

    def _group_notes(body):
        notes = body.get("notes") or {}
        if not isinstance(notes, dict):
            raise HTTPException(400, "Notes must be a list of member notes.")
        return {folder_for(str(k)): v for k, v in notes.items()}

    @app.get("/api/groups")
    def groups_list(request: Request):
        user = require(request)
        out = []
        for i in private_groups.list_for(st.data_dir, user["id"]):
            out.append({"id": i["id"], "name": i["name"],
                        "members": [{"key": m, "name": display_for(m)} for m in i["members"]],
                        "notes": {display_for(k): v for k, v in private_groups.get_notes(st.data_dir, user["id"], i["id"]).items()}})
        return {"groups": out, "max": private_groups.MAX_GROUPS, "min_members": private_groups.MIN_MEMBERS,
                "max_members": private_groups.MAX_MEMBERS, "note_max": private_groups.NOTE_MAX}

    @app.post("/api/groups")
    async def group_create(request: Request):
        _csrf(request)
        user = require(request)
        body = _read_body(await request.json())
        name, members = _group_body(body)
        solo, taken = _group_context(user)
        try:
            item = private_groups.create(st.data_dir, user["id"], name, members, solo, taken, folder_for)
            private_groups.set_notes(st.data_dir, user["id"], item["id"], _group_notes(body), item["members"])
        except private_groups.GroupError as err:
            raise HTTPException(400, str(err))
        return {"id": item["id"], "name": item["name"]}

    @app.put("/api/groups/{gid}")
    async def group_update(gid: str, request: Request):
        _csrf(request)
        user = require(request)
        body = _read_body(await request.json())
        name, members = _group_body(body)
        solo, taken = _group_context(user, exclude=gid)
        try:
            item = private_groups.update(st.data_dir, user["id"], gid, name, members, solo, taken, folder_for)
            private_groups.set_notes(st.data_dir, user["id"], gid, _group_notes(body), item["members"])
        except private_groups.GroupError as err:
            raise HTTPException(400, str(err))
        _drop_slot(st, user, gid)
        return {"id": gid, "name": item["name"]}

    @app.delete("/api/groups/{gid}")
    async def group_delete(gid: str, request: Request):
        _csrf(request)
        user = require(request)
        try:
            private_groups.delete(st.data_dir, user["id"], gid)
        except private_groups.GroupError as err:
            raise HTTPException(400, str(err))
        _drop_slot(st, user, gid)
        return {"ok": True}

    @app.get("/api/history")
    def history(request: Request, character: str):
        user = require(request)
        name, is_group = _resolve_profile(st, character, user)
        slot = _slot(st, user, name, is_group)
        with slot.lock:
            return {"messages": _history(slot.engine)}

    @app.get("/api/context")
    def context_info(request: Request, character: str):
        # Where the next prompt's rolling window starts, and an estimate of its size (see Engine.context_estimate).
        user = require(request)
        name, is_group = _resolve_profile(st, character, user)
        slot = _slot(st, user, name, is_group)
        with slot.lock:
            info = slot.engine.context_estimate()
        info["character"] = _label(st, user, name)
        return info

    @app.post("/api/chat")
    async def chat(request: Request):
        _csrf(request)
        user = require(request)
        body = _read_body(await request.json())
        message = str(body.get("message", "")).strip()
        if not message:
            raise HTTPException(400, "Say something first.")
        if len(message) > MAX_MESSAGE:
            raise HTTPException(400, f"Keep messages under {MAX_MESSAGE} characters.")
        name, is_group = _resolve_profile(st, str(body.get("character", "")), user)
        command = _parse_command(message)
        if command is not None:
            result = await run_in_threadpool(_run_command, st, user, name, is_group, *command)
            return JSONResponse({"command": result})
        _check_caps(st, user)
        slot = await run_in_threadpool(_slot, st, user, name, is_group)
        return StreamingResponse(_run_turn(st, user, name, slot, message),
                                 media_type="text/event-stream", headers=SSE_HEADERS)

    @app.post("/api/autofill")
    async def autofill(request: Request):
        # Draft the user's next message. Not saved to the chat; the user edits it and sends it.
        _csrf(request)
        user = require(request)
        body = _read_body(await request.json())
        name, is_group = _resolve_profile(st, str(body.get("character", "")), user)
        _check_caps(st, user)
        slot = await run_in_threadpool(_slot, st, user, name, is_group)

        def work():
            with slot.lock:
                return slot.engine.autofill()
        try:
            text = await run_in_threadpool(work)
        except RuntimeError as err:
            raise HTTPException(502, f"Auto-fill failed: {err}")
        return {"text": text}

    @app.post("/api/roll")
    async def roll(request: Request):
        # Add a random event to the chat as a narrator note; the next reply takes it into account.
        _csrf(request)
        user = require(request)
        body = _read_body(await request.json())
        name, is_group = _resolve_profile(st, str(body.get("character", "")), user)
        _check_caps(st, user)
        slot = await run_in_threadpool(_slot, st, user, name, is_group)

        def work():
            with slot.lock:
                return slot.engine.restore(slot.engine.roll_event())
        try:
            text = await run_in_threadpool(work)
        except RuntimeError as err:
            raise HTTPException(502, f"Roll failed: {err}")
        return {"event": {"speaker": "Narrator", "text": text}}

    @app.post("/api/regenerate")
    async def regenerate(request: Request):
        # A new reply to the last message. The old reply stays as an alternative (see /api/swipe).
        _csrf(request)
        user = require(request)
        body = _read_body(await request.json())
        name, is_group = _resolve_profile(st, str(body.get("character", "")), user)
        _check_caps(st, user)
        slot = await run_in_threadpool(_slot, st, user, name, is_group)
        return StreamingResponse(_run_turn(st, user, name, slot, "", regen=True),
                                 media_type="text/event-stream", headers=SSE_HEADERS)

    @app.post("/api/swipe")
    async def swipe(request: Request):
        # Show the previous (-1) or next (+1) saved reply to the last message.
        _csrf(request)
        user = require(request)
        body = _read_body(await request.json())
        name, is_group = _resolve_profile(st, str(body.get("character", "")), user)
        direction = body.get("direction")
        if direction not in (-1, 1):
            raise HTTPException(400, "Direction must be -1 or 1.")
        return await run_in_threadpool(_swipe_now, st, user, name, is_group, direction)

    @app.get("/api/memory")
    def memory_get(request: Request, character: str):
        # The memory bank and pinned facts for this account's copy of a character.
        user = require(request)
        name, _ = _resolve_profile(st, character, user)
        mem_path, pin_path = _memory_files(st, user, name)
        return {"memory": _read_or_empty(mem_path), "pinned": _read_or_empty(pin_path), "limit": MEMORY_LIMIT,
                "summary": _read_summary(_summary_path(st, user, name))}

    @app.post("/api/memory")
    async def memory_set(request: Request):
        # Save edits to the memory bank and pinned facts. The next reply loads them.
        _csrf(request)
        user = require(request)
        body = _read_body(await request.json())
        name, _ = _resolve_profile(st, str(body.get("character", "")), user)
        memory = str(body.get("memory", ""))
        pinned = str(body.get("pinned", ""))
        summary_given = "summary" in body
        summary = str(body.get("summary") or "")
        if len(memory) > MEMORY_LIMIT or len(pinned) > MEMORY_LIMIT or len(summary) > MEMORY_LIMIT:
            raise HTTPException(400, f"Keep each part under {MEMORY_LIMIT} characters.")
        mem_path, pin_path = _memory_files(st, user, name)
        sum_path = _summary_path(st, user, name)

        def save_now():
            with st.slots_lock:
                mine = [k for k in st.slots if k[0] == user["id"] and k[1] == name]
                if any(st.slots[k].lock.locked() for k in mine):
                    raise HTTPException(409, "Wait for the current reply to finish before editing memory.")
                _write_text(mem_path, memory)
                _write_text(pin_path, pinned)
                if summary_given:
                    _write_summary(sum_path, summary)
                for k in mine:
                    st.slots.pop(k, None)
        await run_in_threadpool(save_now)
        return {"memory": memory, "pinned": pinned, "summary": _read_summary(sum_path)}

    @app.post("/api/persona")
    async def set_persona(request: Request):
        _csrf(request)
        user = require(request)
        body = _read_body(await request.json())
        choice = folder_for(str(body.get("persona", ""))) if body.get("persona") else ""
        if choice and choice not in load_personas(st.char_root):
            raise HTTPException(400, "Unknown persona.")
        # the chat sessions cache the history in memory; a persona switch rebuilds them from disk,
        # so no reply may be streaming, and no stale cached copy may survive to be saved over newer messages
        with st.slots_lock:
            mine = [k for k in st.slots if k[0] == user["id"]]
            if any(st.slots[k].lock.locked() for k in mine):
                raise HTTPException(409, "Wait for the current reply to finish before changing persona.")
            for k in mine:
                st.slots.pop(k, None)
        store.set_pref(user["id"], "persona", choice)
        return {"persona": _persona_info(st, user), "personas": _persona_list(st)}

    @app.post("/api/settings")
    async def set_settings(request: Request):
        _csrf(request)
        user = require(request)
        body = _read_body(await request.json())
        if "window" in body:
            raw = body["window"]
            if raw in (None, ""):
                value = ""
            else:
                try:
                    n = int(raw)
                except (TypeError, ValueError):
                    raise HTTPException(400, "The window must be a whole number of messages.")
                if not 1 <= n <= 100:
                    raise HTTPException(400, "The window must be between 1 and 100 messages.")
                value = str(n)
            with st.slots_lock:
                if any(st.slots[k].lock.locked() for k in st.slots if k[0] == user["id"]):
                    raise HTTPException(409, "Wait for the current reply to finish before changing the window.")
            store.set_pref(user["id"], "window", value)
        if "thinking" in body:
            store.set_pref(user["id"], "thinking", "1" if body["thinking"] else "0")
        if "model" in body:
            choice = str(body["model"] or "")
            if choice and choice not in _extra_models(st):
                raise HTTPException(400, "That model is not set up on this server.")
            store.set_pref(user["id"], "model", choice)
            with st.slots_lock:
                for k in [k for k in st.slots if k[0] == user["id"]]:
                    st.slots.pop(k, None)
        if "reply_length" in body:
            raw = body["reply_length"]
            if raw in (None, ""):
                value = ""
            else:
                try:
                    n = int(raw)
                except (TypeError, ValueError):
                    raise HTTPException(400, "The reply length must be a whole number of tokens.")
                lo, hi = _reply_bounds(store)
                if not lo <= n <= hi:
                    raise HTTPException(400, f"The reply length must be between {lo} and {hi} tokens.")
                value = str(n)
            with st.slots_lock:
                if any(st.slots[k].lock.locked() for k in st.slots if k[0] == user["id"]):
                    raise HTTPException(409, "Wait for the current reply to finish before changing the reply length.")
            store.set_pref(user["id"], "reply_length", value)
        if "avatar_shape" in body:
            if body["avatar_shape"] not in ("squircle", "circle"):
                raise HTTPException(400, "The icon shape must be squircle or circle.")
            store.set_pref(user["id"], "avatar_shape", body["avatar_shape"])
        # cached engines hold the old settings; drop them so the next turn picks up the new ones
        with st.slots_lock:
            for k in [k for k in st.slots if k[0] == user["id"]]:
                st.slots.pop(k, None)
        return {"thinking": _thinking_on(st, user), "model": store.get_pref(user["id"], "model") or "",
                "window": _pref_int(store.get_pref(user["id"], "window")),
                "reply_length": _reply_target(st, user["id"]),
                "avatar_shape": _avatar_shape(store, user["id"])}

    @app.get("/api/character-settings")
    async def character_settings_get(request: Request, character: str):
        user = require(request)
        name, is_group = _resolve_profile(st, character, user)
        return _chat_settings_payload(st, user, name, is_group)

    @app.post("/api/character-settings")
    async def character_settings_set(request: Request):
        # This account's creativity (temperature) for one character or group. Null or blank resets it.
        _csrf(request)
        user = require(request)
        body = _read_body(await request.json())
        name, is_group = _resolve_profile(st, str(body.get("character", "")), user)
        if "temperature" not in body:
            raise HTTPException(400, "Nothing to save.")
        raw = body["temperature"]
        if raw in (None, ""):
            value = ""
        else:
            try:
                t = float(raw)
            except (TypeError, ValueError):
                raise HTTPException(400, "Creativity must be a number.")
            if not 0.0 <= t <= 1.5:
                raise HTTPException(400, "Creativity must be between 0 and 1.5.")
            value = f"{t:.2f}"
        with st.slots_lock:
            mine = [k for k in st.slots if k[0] == user["id"] and k[1] == name]
            if any(st.slots[k].lock.locked() for k in mine):
                raise HTTPException(409, "Wait for the current reply to finish before changing creativity.")
            for k in mine:
                st.slots.pop(k, None)
        store.set_pref(user["id"], "temp:" + name, value)
        return _chat_settings_payload(st, user, name, is_group)

    @app.post("/api/edit")
    async def edit_message(request: Request):
        # Change the wording of one saved message. No model call, so no request is counted.
        _csrf(request)
        user = require(request)
        body = _read_body(await request.json())
        name, is_group = _resolve_profile(st, str(body.get("character", "")), user)
        try:
            index = int(body.get("index"))
        except (TypeError, ValueError):
            raise HTTPException(400, "Pick a message to edit.")
        text = str(body.get("text", "")).strip()
        if not text:
            raise HTTPException(400, "A message cannot be empty.")
        if len(text) > MAX_MESSAGE:
            raise HTTPException(400, f"Keep messages under {MAX_MESSAGE} characters.")
        slot = await run_in_threadpool(_slot, st, user, name, is_group)

        def apply():
            if not slot.lock.acquire(blocking=False):
                raise HTTPException(409, "Wait for the current reply to finish before editing.")
            try:
                if not slot.engine.edit_message(index, text):
                    raise HTTPException(400, "That message no longer exists.")
                return {"messages": _history(slot.engine)}
            finally:
                slot.lock.release()

        return await run_in_threadpool(apply)

    @app.get("/api/export")
    async def export_chat(request: Request, character: str, format: str = "md"):
        # The chat as a Markdown or plain-text file, saved by the browser.
        user = require(request)
        if format not in ("md", "txt"):
            raise HTTPException(400, "Format must be md or txt.")
        name, is_group = _resolve_profile(st, character, user)
        body = await run_in_threadpool(_export_now, st, user, name, is_group, format)
        stem = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_") or "chat"
        filename = f"{stem}-{time.strftime('%Y-%m-%d')}.{format}"
        media = "text/markdown; charset=utf-8" if format == "md" else "text/plain; charset=utf-8"
        return Response(body, media_type=media,
                        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"})

    @app.post("/api/undo")
    async def undo(request: Request):
        _csrf(request)
        user = require(request)
        body = _read_body(await request.json())
        name, is_group = _resolve_profile(st, str(body.get("character", "")), user)
        return await run_in_threadpool(_undo_now, st, user, name, is_group)

    # ---- admin

    @app.get("/api/admin/users")
    def admin_users(request: Request):
        me = require(request, admin=True)
        today = store.usage_today_all()
        throttle = store.login_throttle_all()
        blocked = store.blocked_ips_all()
        now = time.time()
        out = []
        for u in store.list_users():
            used = today.get(u["id"], {"requests": 0, "tokens": 0})
            out.append({"id": u["id"], "username": u["username"], "is_admin": bool(u["is_admin"]),
                        "twofa": _twofa_state(u), "twofa_required": bool(u["totp_required"]),
                        "disabled": bool(u["disabled"]), "must_change": bool(u["must_change"]),
                        "daily_cap": u["daily_cap"], "effective_cap": _effective_cap(st, u),
                        "requests_today": used["requests"], "tokens_today": used["tokens"],
                        "created_at": u["created_at"], "blocked_ips": blocked.get(u["username"].lower(), []),
                        **_lock_view(throttle.get(u["username"].lower()), now),
                        "token_budgets": {"daily": u.get("token_daily"), "weekly": u.get("token_weekly"),
                                          "monthly": u.get("token_monthly")},
                        "tokens_week": store.usage_tokens(u["id"], budget_days("weekly")),
                        "tokens_month": store.usage_tokens(u["id"], budget_days("monthly"))})
        # me_id lets the admin page offer two-factor set-up on the signed-in account's own row.
        return {"users": out, "global_requests_today": store.global_requests_today(), "me_id": me["id"]}

    @app.post("/api/admin/users")
    async def admin_create(request: Request):
        _csrf(request)
        me_ = require(request, admin=True)
        body = _read_body(await request.json())
        username = str(body.get("username", "")).strip()
        password = str(body.get("password", ""))
        if not USERNAME_RE.match(username):
            raise HTTPException(400, "Usernames are 2-32 letters, digits, _ or -.")
        if len(password) < MIN_PASSWORD:
            raise HTTPException(400, f"Initial password must be at least {MIN_PASSWORD} characters.")
        cap = body.get("daily_cap")
        if cap is not None and (not isinstance(cap, int) or cap < 0):
            raise HTTPException(400, "daily_cap must be a whole number or null.")
        try:
            uid = store.create_user(username, password, is_admin=bool(body.get("is_admin")),
                                    must_change=True, daily_cap=cap)
        except ValueError as err:
            raise HTTPException(400, str(err))
        log(f"user {username} created by {me_['username']}")
        return {"id": uid, "username": username}

    @app.post("/api/admin/users/{uid}")
    async def admin_update(uid: int, request: Request):
        _csrf(request)
        me_ = require(request, admin=True)
        target = store.get_user(uid)
        if target is None:
            raise HTTPException(404, "No such user.")
        body = _read_body(await request.json())
        is_self = target["id"] == me_["id"]
        losing_admin = bool(target["is_admin"]) and (body.get("is_admin") is False or body.get("disabled") is True)
        if is_self and losing_admin:
            raise HTTPException(400, "You can't demote or disable your own admin account.")
        if losing_admin and store.count_enabled_admins() <= 1:
            raise HTTPException(400, "That would leave no enabled admin.")

        fields = {}
        if "is_admin" in body:
            fields["is_admin"] = int(bool(body["is_admin"]))
        if "disabled" in body:
            fields["disabled"] = int(bool(body["disabled"]))
        if "daily_cap" in body:
            cap = body["daily_cap"]
            if cap is not None and (not isinstance(cap, int) or cap < 0):
                raise HTTPException(400, "daily_cap must be a whole number or null.")
            fields["daily_cap"] = cap
        for key in ("token_daily", "token_weekly", "token_monthly"):
            if key in body:
                val = body[key]
                if val is not None and (not isinstance(val, int) or isinstance(val, bool) or val < 0):
                    raise HTTPException(400, f"{key} must be a whole number of tokens, or null for no budget.")
                fields[key] = val
        if body.get("reset_password"):
            new = str(body["reset_password"])
            if len(new) < MIN_PASSWORD:
                raise HTTPException(400, f"Password must be at least {MIN_PASSWORD} characters.")
            store.set_password(uid, new, must_change=True)
            store.end_all_sessions(uid)
        if body.get("unlock_sign_in"):
            store.unlock_account(target["username"])
            log(f"sign-in lock cleared for {target['username']} by {me_['username']}")
        if "require_2fa" in body:
            # Requiring it turns the account's two-factor sign-in on; it's set up at the next password sign-in.
            # Requiring it on an account that isn't set up yet ends its sessions: it must finish the set-up page first.
            if body["require_2fa"] and _twofa_state(target) != "on":
                store.end_all_sessions(uid)
            store.set_totp_required(uid, bool(body["require_2fa"]))
        if body.get("reset_2fa"):
            # Lost phone: the account sets up a new code at its next sign-in, and every session ends.
            store.set_totp(uid, None, bool(store.get_user(uid)["totp_required"]), False, -1)
            store.end_all_sessions(uid)
            log(f"two-factor sign-in reset for {target['username']} by {me_['username']}")
        if fields:
            store.update_user(uid, **fields)
        if body.get("disabled") is True:
            store.end_all_sessions(uid)
        log(f"user {target['username']} updated by {me_['username']}: {sorted(body)}")
        return {"ok": True}

    # ---- two-factor sign-in for the signed-in account

    @app.get("/api/2fa")
    def twofa_status(request: Request):
        user = require(request, allow_change=True)
        return {"state": _twofa_state(user), "required": bool(user["totp_required"])}

    @app.post("/api/2fa/start")
    async def twofa_start(request: Request):
        # A new secret for the app. Two-factor sign-in stays off until a code from the app is confirmed.
        _csrf(request)
        user = require(request, allow_change=True)
        if _twofa_state(user) == "on":
            raise HTTPException(400, "Two-factor sign-in is already on.")
        secret = secure.new_totp_secret()
        store.set_totp(user["id"], secure.seal(_server_key(), secret, _totp_aad(user["id"])),
                       bool(user["totp_required"]), False, -1)
        return {"secret": secret, "qr": secure.qr_svg(secure.provisioning_uri(secret, user["username"]))}

    @app.post("/api/2fa/confirm")
    async def twofa_confirm(request: Request):
        _csrf(request)
        user = require(request, allow_change=True)
        body = _read_body(await request.json())
        if not user["totp_secret"] or user["totp_confirmed"]:
            raise HTTPException(400, "Start two-factor sign-in first.")
        step = _check_session_code(user, body.get("code"))
        store.set_totp(user["id"], user["totp_secret"], True, True, step)
        log(f"two-factor sign-in turned on for {user['username']}")
        return {"ok": True}

    @app.post("/api/2fa/disable")
    async def twofa_disable(request: Request):
        _csrf(request)
        user = require(request, allow_change=True)
        body = _read_body(await request.json())
        if user["totp_required"]:
            raise HTTPException(403, "An admin requires two-factor sign-in on this account.")
        if _twofa_state(user) != "on":
            raise HTTPException(400, "Two-factor sign-in is not on.")
        _session_gate(user)
        if store.authenticate(user["username"], str(body.get("password", ""))) is None:
            _session_miss(user, "Wrong password.")
        _check_session_code(user, body.get("code"))
        store.set_totp(user["id"], None, False, False, -1)
        log(f"two-factor sign-in turned off for {user['username']}")
        return {"ok": True}

    # ---- encrypted backups: admins with two-factor sign-in on; one backup password, set once

    def _backup_admin(user, code):
        if _twofa_state(user) != "on":
            raise HTTPException(403, "Turn on two-factor sign-in for your admin account before making backups.")
        store.set_totp_step(user["id"], _check_session_code(user, code))

    @app.get("/api/admin/backup")
    def backup_status(request: Request):
        user = require(request, admin=True)
        return {"password_set": store.backup_verifier() is not None, "two_factor_on": _twofa_state(user) == "on"}

    @app.post("/api/admin/backup/password")
    async def backup_password_set(request: Request):
        _csrf(request)
        user = require(request, admin=True)
        body = _read_body(await request.json())
        _backup_admin(user, body.get("code"))
        pw = str(body.get("password", ""))
        if not secure.new_backup_password_ok(pw):
            raise HTTPException(400, "The backup password needs at least 12 characters.")
        verifier = await run_in_threadpool(secure.backup_verifier, pw)
        if not store.set_backup_verifier_once(verifier):
            raise HTTPException(409, "The backup password is already set.")
        log(f"backup password set by {user['username']}")
        return {"ok": True}

    @app.post("/api/admin/backup")
    async def backup_make(request: Request):
        _csrf(request)
        user = require(request, admin=True)
        body = _read_body(await request.json())
        kind = str(body.get("kind", ""))
        if kind not in backup.KINDS:
            raise HTTPException(400, "Choose a data backup or a full backup.")
        _backup_admin(user, body.get("code"))
        verifier = store.backup_verifier()
        if verifier is None:
            raise HTTPException(409, "Set the backup password first.")
        pw = str(body.get("password", ""))
        _session_gate(user)
        if not await run_in_threadpool(secure.check_backup_verifier, pw, verifier):
            _session_miss(user, "Wrong backup password.")
        _session_code_fails.pop(user["id"], None)
        try:
            blob = await run_in_threadpool(_build_backup, st, kind, pw)
        except backup.BackupError as err:
            raise HTTPException(413, str(err))
        log(f"{kind} backup made by {user['username']}")
        name = f"miso-{kind}-backup-{time.strftime('%Y%m%d-%H%M')}.mbk"
        return Response(content=blob, media_type="application/octet-stream",
                        headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})

    # ---- character editor (admin only; friends see icons and colours, never the card)

    @app.get("/api/admin/tags")
    def tag_list(request: Request):
        # Every tag in use and how many characters carry it. Feeds the editor's suggestions.
        require(request)
        solo, _ = scan_characters(st.char_root)
        return {"tags": profiles.tag_counts(st.char_root, solo)}

    @app.get("/api/admin/characters/example")
    def editor_example(request: Request):
        require(request)
        return {"card": editor.example_card()}

    @app.post("/api/admin/characters/draft")
    async def editor_draft(request: Request):
        # Drafts a headline, summary and tags from a card. Nothing is saved until the form is saved.
        _csrf(request)
        user = require(request)
        body = _read_body(await request.json())
        card = body.get("card", "")
        if not isinstance(card, str) or not card.strip():
            raise HTTPException(400, "Write the character card first.")
        _check_caps(st, user)

        def work():
            return profiles.generate(st.client, card, on_call=lambda model: _count_model_call(st, user["id"], model),
                                     gate=st.profile_gate, wait_timeout=30, priority=True,
                                     known_tags=_known_tags(st))
        try:
            prof = await run_in_threadpool(work)
        except profiles.ProfileBusy as err:
            raise HTTPException(429, f"Auto-fill is busy, try again in a minute ({err}).")
        except profiles.ProfileError as err:
            raise HTTPException(502, f"Auto-fill failed: {err}")
        return {"headline": prof["headline"], "info": prof["info"], "tags": prof["tags"]}

    @app.get("/api/admin/characters/{name}")
    def editor_read(name: str, request: Request):
        _require_editor(require(request), name)
        try:
            return editor.read_card(st.char_root, name)
        except editor.EditorError as err:
            raise HTTPException(404, str(err))

    @app.post("/api/admin/characters")
    async def editor_create(request: Request):
        _csrf(request)
        user = require(request)
        body = _read_body(await request.json())
        try:
            out = editor.write_card(st.char_root, str(body.get("name", "")), body.get("card", ""),
                                    body.get("context", ""), body.get("colour", ""), creating=True,
                                    sandbox_on=(bool(body["sandbox"]) if ("sandbox" in body and user["is_admin"]) else None),
                                    profile=body.get("profile"))
            editor.record_creator(editor.folder_of(st.char_root, str(body.get("name", ""))), user["username"])
        except editor.EditorError as err:
            raise HTTPException(400, str(err))
        return out

    @app.put("/api/admin/characters/{name}")
    async def editor_update(name: str, request: Request):
        _csrf(request)
        user = require(request)
        _require_editor(user, name)
        body = _read_body(await request.json())
        try:
            out = editor.write_card(st.char_root, name, body.get("card", ""),
                                    body.get("context", ""), body.get("colour", ""), creating=False,
                                    sandbox_on=(bool(body["sandbox"]) if ("sandbox" in body and user["is_admin"]) else None),
                                    profile=body.get("profile"))
        except editor.EditorError as err:
            raise HTTPException(400, str(err))
        with st.slots_lock:
            for k in [k for k in st.slots if k[1] == folder_for(name)]:
                st.slots.pop(k, None)   # cached engines hold the old card
        return out

    @app.post("/api/admin/characters/{name}/icon")
    async def editor_icon(name: str, request: Request):
        _csrf(request)
        _require_editor(require(request), name)
        data = await request.body()
        try:
            editor.save_icon(st.char_root, name, data)
        except editor.EditorError as err:
            raise HTTPException(400, str(err))
        return {"ok": True}

    @app.get("/api/icon/{name}")
    def character_icon(name: str, request: Request):
        require(request)
        try:
            path = editor.icon_path(st.char_root, name)
        except editor.EditorError:
            raise HTTPException(404, "No such character.")
        if path is None:
            raise HTTPException(404, "No icon.")
        media = {".webp": "image/webp", ".png": "image/png", ".jpg": "image/jpeg"}.get(os.path.splitext(path)[1].lower())
        return FileResponse(path, media_type=media, headers={"Cache-Control": "private, max-age=300"})

    @app.get("/api/icon/{name}/hash")
    def character_icon_hash(name: str, request: Request):
        require(request)
        try:
            path = editor.icon_path(st.char_root, name)
        except editor.EditorError:
            raise HTTPException(404, "No such character.")
        if path is None:
            raise HTTPException(404, "No icon.")
        digest = hashlib.sha256()
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(65536), b""):
                digest.update(block)
        return {"sha256": digest.hexdigest()}

    @app.get("/api/version")
    def server_version():
        # Public on purpose: the terminal client reads it before it logs in.
        return {"version": APP_VERSION}

    @app.get("/api/client/miso_remote.py")
    def terminal_client_download(request: Request):
        require(request)
        # The client ships in the repository's client folder, beside web/.
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(root, "client", "miso_remote.py")
        if not os.path.isfile(path):
            raise HTTPException(404, "The terminal client is not in this install.")
        return FileResponse(path, media_type="text/x-python", filename="miso_remote.py")

    @app.post("/api/admin/characters/import")
    async def editor_import(request: Request):
        _csrf(request)
        user = require(request)
        data = await request.body()
        try:
            return await run_in_threadpool(transfer.import_zip, st.char_root, st.data_dir, user["id"],
                                           user["username"], data, admin=bool(user["is_admin"]))
        except editor.EditorError as err:
            raise HTTPException(400, str(err))

    @app.get("/api/admin/characters/{name}/export")
    async def editor_export(name: str, request: Request, history: str = "0"):
        # A zip of the character. history=1 adds this admin's chat history and memories for it.
        user = require(request)
        _require_editor(user, name)
        include = history in ("1", "true")
        try:
            body = await run_in_threadpool(transfer.export_zip, st.char_root, st.data_dir, user["id"], name, include)
        except editor.EditorError as err:
            raise HTTPException(404, str(err))
        stem = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_") or "character"
        suffix = "-with-history" if include else ""
        return Response(body, media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{stem}{suffix}.zip"',
                                 "Cache-Control": "no-store"})

    @app.post("/api/admin/characters/{name}/background")
    async def editor_background(name: str, request: Request):
        _csrf(request)
        _require_editor(require(request), name)
        data = await request.body()
        try:
            editor.save_background(st.char_root, name, data)
        except editor.EditorError as err:
            raise HTTPException(400, str(err))
        return {"ok": True}

    @app.get("/api/background/{name}")
    def character_background(name: str, request: Request):
        require(request)
        try:
            path = editor.background_path(st.char_root, name)
        except editor.EditorError:
            raise HTTPException(404, "No such character.")
        if path is None:
            raise HTTPException(404, "No background.")
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=300"})

    @app.get("/api/admin/providers")
    def admin_providers_get(request: Request):
        require(request, admin=True)
        return providers.public_view(providers.load(data_dir))

    @app.post("/api/admin/providers")
    async def admin_providers_set(request: Request):
        _csrf(request)
        require(request, admin=True)
        body = _read_body(await request.json())
        cfg = providers.update(data_dir, body)
        st.client = build_client(client, data_dir)   # rebuilt from the new settings
        with st.slots_lock:
            st.slots.clear()                           # cached engines still hold the old client
        return providers.public_view(cfg)

    @app.get("/api/admin/settings")
    def admin_settings_get(request: Request):
        require(request, admin=True)
        return store.all_settings()

    @app.post("/api/admin/settings")
    async def admin_settings_set(request: Request):
        _csrf(request)
        require(request, admin=True)
        body = _read_body(await request.json())
        for key, val in body.items():
            if key not in DEFAULT_SETTINGS:
                raise HTTPException(400, f"Unknown setting: {key}")
            if key in SWITCHES:
                if not isinstance(val, bool):
                    raise HTTPException(400, f"{key} must be on or off.")
                continue
            if not isinstance(val, int) or isinstance(val, bool) or val < 1:
                raise HTTPException(400, f"{key} must be a whole number of at least 1.")
        lo = int(body.get("reply_min_tokens", store.get_setting("reply_min_tokens")))
        hi = int(body.get("reply_max_tokens", store.get_setting("reply_max_tokens")))
        if not 10 <= lo <= hi <= 4000:
            raise HTTPException(400, "Reply length must be between 10 and 4000 tokens, and the shortest cannot be longer than the longest.")
        for key, val in body.items():
            store.set_setting(key, ("1" if val else "0") if key in SWITCHES else val)
        return store.all_settings()

    def _character_dir(name):
        try:
            folder = editor.folder_of(st.char_root, name)
        except editor.EditorError as err:
            raise HTTPException(400, str(err))
        if not os.path.isdir(folder):
            raise HTTPException(404, "Unknown character.")
        return folder

    @app.get("/api/admin/characters/{name}/palace")
    def palace_get(name: str, request: Request, user: str = ""):
        # No user: the character's starting palace, for its editors. With user=<id>: an admin reads one non-admin's palace.
        me_ = require(request)
        folder = _character_dir(name)
        if user:
            if not me_["is_admin"]:
                raise HTTPException(403, "Admins only.")
            try:
                target = st.store.get_user(int(user))
            except ValueError:
                target = None
            if target is None:
                raise HTTPException(404, "No such account.")
            if target["is_admin"]:
                raise HTTPException(400, "Admin palaces are not shown here.")
            entries, cfg = palace.load_all(_account_palace_dir(target["id"], os.path.basename(folder)))
        else:
            _require_editor(me_, name)
            entries, cfg = palace.load_all(folder)
        return {"entries": entries, "recursive": cfg["recursive"], "budget": cfg["budget"]}

    @app.post("/api/admin/characters/{name}/palace")
    async def palace_set(name: str, request: Request):
        _csrf(request)
        _require_editor(require(request), name)
        body = _read_body(await request.json())
        settings = {"recursive": body.get("recursive"), "budget": body.get("budget")}
        try:
            entries, cfg = palace.save(_character_dir(name), body.get("entries", []), settings)
        except palace.PalaceError as err:
            raise HTTPException(400, str(err))
        return {"entries": entries, "recursive": cfg["recursive"], "budget": cfg["budget"]}

    def _require_editor(user, name):
        # Creators change their own characters; a character made as 'admin' is changed by any admin.
        if not can_edit_card(user, _character_dir(name)):
            raise HTTPException(403, "Only the character's creator can change it. Characters made as admin can be changed by any admin.")

    def _account_palace_dir(uid, folder):
        return os.path.join(st.data_dir, "users", str(uid), folder)

    def _solo_folder(user, character):
        # Groups have no Mind Palace of their own.
        name, is_group = _resolve_profile(st, character, user)
        if is_group:
            raise HTTPException(400, "Groups do not have a Mind Palace.")
        return name

    @app.get("/api/character-palace")
    def my_palace_get(request: Request, character: str):
        # This account's own palace for one character. The first read copies the character's starting palace.
        user = require(request)
        folder = _solo_folder(user, character)
        pdir = _account_palace_dir(user["id"], folder)
        palace.copy_starting(os.path.join(st.char_root, folder), pdir)
        entries, cfg = palace.load_all(pdir)
        return {"entries": entries, "recursive": cfg["recursive"], "budget": cfg["budget"]}

    @app.post("/api/character-palace")
    async def my_palace_set(request: Request, character: str):
        _csrf(request)
        user = require(request)
        folder = _solo_folder(user, character)
        body = _read_body(await request.json())
        pdir = _account_palace_dir(user["id"], folder)
        os.makedirs(pdir, exist_ok=True)
        settings = {"recursive": body.get("recursive"), "budget": body.get("budget")}
        try:
            entries, cfg = palace.save(pdir, body.get("entries", []), settings)
        except palace.PalaceError as err:
            raise HTTPException(400, str(err))
        return {"entries": entries, "recursive": cfg["recursive"], "budget": cfg["budget"]}

    @app.post("/api/admin/character-draft")
    async def character_draft(request: Request):
        # Reads one uploaded file and drafts the card from its text. Nothing is saved here.
        from urllib.parse import unquote
        _csrf(request)
        user = require(request)
        if int(request.headers.get("content-length") or 0) > ocr.MAX_BYTES + 4096:
            raise HTTPException(413, "Files can be up to 10 MB.")
        _check_caps(st, user)
        data = await request.body()
        filename = unquote(request.headers.get("x-filename", ""))
        try:
            text, notes = await run_in_threadpool(ocr.extract, filename, data)
        except ocr.OCRError as err:
            raise HTTPException(400, str(err))

        def work():
            return draft.draft_character(st.client, text,
                                         on_call=lambda model: _count_model_call(st, user["id"], model),
                                         gate=st.profile_gate, wait_timeout=30, priority=True)
        try:
            result = await run_in_threadpool(work)
        except profiles.ProfileBusy as err:
            raise HTTPException(429, str(err))
        except draft.DraftError as err:
            raise HTTPException(502, f"Draft failed: {err}")
        result["notes"] = notes
        return result

    def _branch_root(user, name):
        return os.path.join(st.data_dir, "users", str(user["id"]), name)

    def _branch_state(user, name, is_group):
        # After a branch change the next read loads the chat from the newly active folder.
        _drop_slot(st, user, name)
        slot = _slot(st, user, name, is_group)
        with slot.lock:
            return {"messages": _history(slot.engine), **branches.listing(_branch_root(user, name))}

    @app.get("/api/branches")
    def branch_list(request: Request, character: str):
        user = require(request)
        name, _ = _resolve_profile(st, character, user)
        return branches.listing(_branch_root(user, name))

    @app.post("/api/branch")
    async def branch_fork(request: Request):
        # Copies the chat up to the chosen message and opens the copy. The main chat is left as it was.
        _csrf(request)
        user = require(request)
        body = _read_body(await request.json())
        name, is_group = _resolve_profile(st, str(body.get("character", "")), user)

        def work():
            slot = _slot(st, user, name, is_group)
            if not slot.lock.acquire(blocking=False):
                raise HTTPException(409, "Wait for the current reply to finish before branching.")
            try:
                try:
                    branches.fork(_branch_root(user, name), body.get("index"), str(body.get("name") or ""))
                except branches.BranchError as err:
                    raise HTTPException(400, str(err))
            finally:
                slot.lock.release()
            return _branch_state(user, name, is_group)
        return await run_in_threadpool(work)

    @app.post("/api/branch/switch")
    async def branch_switch(request: Request):
        _csrf(request)
        user = require(request)
        body = _read_body(await request.json())
        name, is_group = _resolve_profile(st, str(body.get("character", "")), user)

        def work():
            slot = _slot(st, user, name, is_group)
            if not slot.lock.acquire(blocking=False):
                raise HTTPException(409, "Wait for the current reply to finish before switching branches.")
            try:
                try:
                    branches.switch(_branch_root(user, name), str(body.get("id", "")))
                except branches.BranchError as err:
                    raise HTTPException(400, str(err))
            finally:
                slot.lock.release()
            return _branch_state(user, name, is_group)
        return await run_in_threadpool(work)

    @app.post("/api/branch/delete")
    async def branch_delete(request: Request):
        _csrf(request)
        user = require(request)
        body = _read_body(await request.json())
        name, is_group = _resolve_profile(st, str(body.get("character", "")), user)

        def work():
            slot = _slot(st, user, name, is_group)
            if not slot.lock.acquire(blocking=False):
                raise HTTPException(409, "Wait for the current reply to finish before deleting a branch.")
            try:
                try:
                    branches.delete(_branch_root(user, name), str(body.get("id", "")))
                except branches.BranchError as err:
                    raise HTTPException(400, str(err))
            finally:
                slot.lock.release()
            return _branch_state(user, name, is_group)
        return await run_in_threadpool(work)

    @app.get("/api/branches/tree")
    def branch_tree(request: Request, character: str):
        user = require(request)
        name, _ = _resolve_profile(st, character, user)
        return branches.tree(_branch_root(user, name))

    @app.get("/api/admin/branches/tree")
    def admin_branch_tree(request: Request, uid: int, character: str):
        # Read-only for admins: shows another account's branch tree and changes nothing.
        require(request, admin=True)
        target = st.store.get_user(uid)
        if target is None:
            raise HTTPException(404, "Unknown account.")
        name, _ = _resolve_profile(st, character, target)
        return {"account": target["username"], **branches.tree(_branch_root(target, name))}

    return app
