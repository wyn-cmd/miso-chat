# Chat engine for Miso Chat.
#
# Model calls, sliding windows, the group director, the request and token
# trackers, and the memory.md refresh live here. Nothing in this module prints
# or reads keyboard input. Callers decide how results are shown.
# (terminal) uses it now, and the web app will use it next.
#
# Paths are relative to the working directory, as they were in the original
# script, so the character folders keep their layout.

import os
import re
import json
import time
import random
import copy
import shutil
import threading
import datetime
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytz
from google.genai import types
from charname import display_for
import sandbox
from web import palace


def _mk(text):
    # Escape Rich markup brackets so a name such as 'Fox [Night]' prints as typed.
    return str(text).replace("[", "\\[")


CONFIG_FILE = "config.json"
REQUEST_TRACKER_FILE = "request_tracker.json"
TOKEN_TRACKER_FILE = "token_tracker.json"
GLOBAL_SUBS_FILE = "substitutions.json"
TMP_SUFFIX = ".tmp"

REQUEST_LIMIT = 14400
TOKEN_LIMIT = 1000000
SOLO_WINDOW = 25        # messages sent to the model in 1:1 chats
GROUP_WINDOW = 40       # messages sent to each group member
SELECTOR_WINDOW = 60    # messages the group director reads
MAX_TOOL_ROUNDS = 8     # file-tool round trips allowed in one solo reply
SANDBOX_NOTE = (
    "\n\n--- Your sandbox ---\n"
    "You have a private folder of your own. Its top level is '/'. Using the file tools you can list, read, "
    "write, copy, move and delete files there, and make folders. Nothing outside it is yours to use. "
    "Use it when it helps, for example to keep notes between conversations. "
    "Don't mention the tools unless it fits the scene."
)

AUTOFILL_SYSTEM = (
    "You write the next message the user would send in this roleplay chat. Write it as the user, in first person, "
    "using the user's persona where one is given. Respond to the latest messages and move the scene forward. "
    "Keep it to one to three sentences. Output only the message text, with no quotes, labels or notes."
)
ROLL_SYSTEM = (
    "You add one random event to this roleplay scene. Choose something that fits the setting and the recent messages, "
    "and that gives the characters something to react to. Write it as a short narrator note in the third person, "
    "present tense, one to three sentences. Do not write dialogue or actions for the user's character. "
    "Output only the note text."
)


def _clean_plain(text):
    # Model output for the chat box or the narrator note: trimmed, wrapping quotes removed, capped in length.
    t = (text or "").strip()
    if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'\u201c\u201d":
        t = t[1:-1].strip()
    return t[:1000]
MEMORY_EVERY = 18       # refresh memory.md every N messages
MEMORY_SNIPPET = 30     # messages fed into a memory.md refresh
SOLO_TEMPERATURE = 0.85     # creativity for 1:1 chats unless the user sets their own
GROUP_TEMPERATURE = 0.7     # group members and the group director
SUMMARY_MAX_MESSAGES = 60   # most messages folded into the running summary in one refresh
MAX_REGENERATIONS = 30  # regenerations allowed per message; the original reply is kept too, so 31 replies at most

# Every model id the app may use. Older Gemini ids are retired and are dropped from the chain, the picker and any saved
# last model. Flash is 3.8 only; flash-lite is 3.5 and 3.1; Gemma 4 31B is unchanged. Add an id here to bring it back.
ALLOWED_MODELS = ["gemini-3.8-flash", "gemma-4-31b-it", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
DEFAULT_FALLBACKS = [
    "gemini-3.8-flash",
    "gemma-4-31b-it",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
]
PRIORITY_MODELS = ["gemini-3.8-flash", "gemma-4-31b-it"]
MEMORY_MODELS_HEAD = ["gemini-3.8-flash", "gemma-4-31b-it"]
MEMORY_MODELS_TAIL = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]

DEFAULT_SUBSTITUTIONS = {}

NARRATIVE_INSTRUCTION = (
    "\n\nCRITICAL NARRATIVE INSTRUCTION: Adopt a high-literary, impressionistic style. "
    "Use abstract, poetic language and focus on sensory metaphors. Prioritize atmosphere "
    "and emotional resonance over explicit physical detail. Favor subtext and artistic "
    "expression. BE HIGHLY DESCRIPTIVE AND ELABORATE IN YOUR RESPONSES. PROVIDE EXTENSIVE "
    "DETAIL, LENGTHY NARRATIVE BLOCKS, AND DEEP SENSORY IMAGERY IN EVERY TURN. MAINTAIN THE "
    "EMPHASIS ON SUBTEXT AND ARTISTIC EXPRESSION THROUGHOUT YOUR NARRATIVE."
)
DIALOGUE_INSTRUCTION = (
    "\n\nDIALOGUE FORMATTING INSTRUCTION: Always enclose all spoken dialogue and direct "
    "speech clearly within quotation marks (\"), and optionally highlight or distinguish "
    "spoken lines so they stand out distinctly in your narrative."
)
FORMAT_INSTRUCTION = (
    "\n\nFORMATTING INSTRUCTION: Write in Markdown. Put descriptions and narration in *italics*. "
    "Put speech and dialogue in double quotation marks, like \"this\". Show emphasis in **bold**. "
    "Dialogue may also be in *italics* when it is a whisper, a thought, or a line that must stand apart. "
    "Put the asterisks directly against the words with no space inside them, and close every marker you open."
)
GROUP_RULE = (
    "\n\nCRITICAL: You are part of a group chat. Provide a unique response to the user's input. "
    "Do not repeat what other group members have already said in this turn. Focus on your "
    "specific character's perspective."
)
MEMORY_PROMPT = (
    "You are an expert narrative architect. Update and evolve the existing memory bank in Markdown format (memory.md) using the provided new roleplay history snippet (the last 10 messages). "
    "Incorporate new developments while preserving key long-term facts, relationships, and lore from the existing memory bank.\n"
    "CRITICAL: Maintain a comprehensive list of ALL distinct locations visited or mentioned throughout the roleplay, not just the most recent one. Track the state and events associated with each location.\n"
    "Include sections like:\n"
    "- # Locations (Keep a detailed, persistent log of all locations)\n"
    "- # Relationships\n"
    "- # Major Plot Points\n"
    "- # Key Facts\n"
    "- # Secrets / Hidden Motivations\n"
    "Make it detailed, immersive, and structured so the AI can maintain continuity.\n"
    "Return ONLY the markdown content. Do not include introductory conversational filler."
)


# ---------------------------------------------------------------- helpers

def _read_text(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _write_json(path, data, **kw):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, **kw)


def _read_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return default


_WORD_RE_CACHE = {}


def _whole_word(word):
    # Pattern matching a word or phrase only when not inside a longer word (so 'ass' skips 'class').
    pat = _WORD_RE_CACHE.get(word)
    if pat is None:
        pat = re.compile(r"(?<![A-Za-z0-9])" + re.escape(word) + r"(?![A-Za-z0-9])", re.IGNORECASE)
        _WORD_RE_CACHE[word] = pat
    return pat


PROVIDER_PREFIXES = ("deepseek:", "local:", "claude:")
ZERO_BUDGET_OK = ("3.1-flash-lite",)   # verified to accept thinking_budget=0; other Gemini models are untested


def thinking_config_for(model, enabled):
    # Thinking settings for one model. Off by default.
    #
    # OpenAI-style and Claude Code backends read include_thoughts only. Gemma accepts no thinking
    # config at all. Gemini models that refuse a budget of 0 get include_thoughts=False with no budget,
    # so any thinking they do stays hidden.
    if model.startswith(PROVIDER_PREFIXES):
        return types.ThinkingConfig(include_thoughts=bool(enabled))
    if "gemma" in model:
        return None
    if enabled:
        return types.ThinkingConfig(include_thoughts=True, thinking_budget=-1)
    if any(tag in model for tag in ZERO_BUDGET_OK):
        return types.ThinkingConfig(include_thoughts=False, thinking_budget=0)
    return types.ThinkingConfig(include_thoughts=False)


def _thought_text(resp):
    # Reasoning text of one response or chunk, or None. Works for Gemini parts and adapter objects.
    t = getattr(resp, "thought", None)
    if isinstance(t, str):
        return t or None
    try:
        parts = resp.candidates[0].content.parts or []
    except Exception:
        return None
    out = [p.text for p in parts if getattr(p, "thought", False) and getattr(p, "text", None)]
    return "".join(out) or None


def _text(resp):
    # Response text, or None when the response is empty or has no text.
    try:
        return resp.text if resp is not None and resp.text else None
    except Exception:
        return None


def _usage(usage, attr):
    if usage and hasattr(usage, attr):
        return getattr(usage, attr)
    return "?"


def get_safe_color(col, fallback="#00ffff"):
    if not col:
        return fallback
    col = col.strip().lower()
    mapping = {
        "silver": "#C0C0C0", "amber": "#FFB000", "gold": "#FFD700", "orange": "#FFA500",
        "red": "#FF4500", "green": "#00FF00", "blue": "#0000FF", "cyan": "#00FFFF",
        "magenta": "#FF00FF", "yellow": "#FFFF00", "brown": "#A52A2A", "purple": "#800080",
        "pink": "#FFC0CB",
    }
    if col.startswith("#"):
        return col
    return mapping.get(col, fallback)


def timestamp_now():
    now = datetime.datetime.now()
    hour = str(int(now.strftime("%I")))
    ampm = "p.m." if now.strftime("%p").lower() == "pm" else "a.m."
    return f"{now.day}/{now.month}/{now.strftime('%y')} {hour}:{now.strftime('%M')} {ampm}"


def safety_settings():
    dangerous = (types.HarmCategory.DANGEROUS_CONTENT
                 if hasattr(types.HarmCategory, "DANGEROUS_CONTENT")
                 else types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT)
    return [
        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=types.HarmBlockThreshold.BLOCK_NONE),
        types.SafetySetting(category=dangerous, threshold=types.HarmBlockThreshold.BLOCK_NONE),
    ]


# ---------------------------------------------------------------- history

def _load_json_file(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _write_history(path, history):
    # Write the history as it is. Regenerate and swipe remove turns in memory on purpose.
    tmp = path + TMP_SUFFIX
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


# ---------------------------------------------------------------- trackers

_TRACKER_LOCK = threading.RLock()


def _locked(fn):
    # Serialise tracker file read/writes: the web app runs requests on threads.
    def wrapper(*args, **kwargs):
        with _TRACKER_LOCK:
            return fn(*args, **kwargs)
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


def _now_iso():
    # When a message was sent or finished, in UTC; the page shows it in the reader's local time.
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def pacific_date():
    return datetime.datetime.now(pytz.timezone("US/Pacific")).strftime("%d/%m/%y")


@_locked
def check_and_update_requests(path=REQUEST_TRACKER_FILE):
    today = pacific_date()
    data = _read_json(path, {"date": today, "requests_made": 0})
    if data.get("date") != today:
        data = {"date": today, "requests_made": 0}
    data["requests_made"] = data.get("requests_made", 0) + 1
    _write_json(path, data)
    return data["requests_made"]


@_locked
def get_request_bar(path=REQUEST_TRACKER_FILE):
    today = pacific_date()
    data = _read_json(path, {"date": today, "requests_made": 0})
    if data.get("date") != today:
        data = {"date": today, "requests_made": 0}
    count = data.get("requests_made", 0)
    pct = min(100.0, (count / REQUEST_LIMIT) * 100.0)
    bar_width = 20
    filled = round(bar_width * (pct / 100.0))
    bar_str = "█" * filled + "░" * (bar_width - filled)
    return f"[{bar_str}] {pct:.1f}% ({count}/{REQUEST_LIMIT})"


@_locked
def check_and_update_tokens(turn_tokens, path=TOKEN_TRACKER_FILE):
    # Add one turn's tokens to today's total. Returns (used, limit, est_msgs_left, recent).
    today = datetime.datetime.now().strftime("%d/%m/%y")
    data = _read_json(path, {"date": today, "tokens_used": 0, "token_limit": TOKEN_LIMIT, "recent_turns": []})

    try:
        saved_date = datetime.datetime.strptime(data.get("date", today), "%d/%m/%y").date()
        if datetime.datetime.now().date() > saved_date:
            data["date"] = today
            data["tokens_used"] = 0
            data["recent_turns"] = []
    except Exception:
        data["date"] = today
        data["tokens_used"] = 0
        data["recent_turns"] = []

    if "recent_turns" not in data or not isinstance(data["recent_turns"], list):
        data["recent_turns"] = []

    if isinstance(turn_tokens, int) and turn_tokens > 0:
        data["tokens_used"] += turn_tokens
        data["recent_turns"].append(turn_tokens)
        data["recent_turns"] = data["recent_turns"][-5:]

    data.setdefault("token_limit", TOKEN_LIMIT)
    _write_json(path, data)

    tokens_used = data["tokens_used"]
    token_limit = data["token_limit"]
    tokens_left = max(0, token_limit - tokens_used)
    recent = data["recent_turns"]

    est_msgs_left = "?"
    if len(recent) > 0:
        avg_cost = sum(recent) / len(recent)
        if len(recent) >= 2:
            deltas = [recent[i] - recent[i - 1] for i in range(1, len(recent))]
            ratios = [recent[i] / recent[i - 1] for i in range(1, len(recent)) if recent[i - 1] > 0]
            avg_delta = sum(deltas) / len(deltas)
            avg_ratio = sum(ratios) / len(ratios) if ratios else 1.0
            if avg_ratio > 1.1:
                predicted_next_cost = recent[-1] * avg_ratio
            else:
                predicted_next_cost = recent[-1] + avg_delta
            avg_cost = (recent[-1] + predicted_next_cost) / 2
        avg_cost = max(100, avg_cost)
        if avg_cost > 0:
            est_msgs_left = int(tokens_left / avg_cost)

    return tokens_used, token_limit, est_msgs_left, recent


# ---------------------------------------------------------------- results

@dataclass
class Reply:
    speaker: str
    color: str
    text: str           # raw text, safe words, as stored in history
    display: str        # with the original words restored
    model: str
    request_bar: str
    thinking: str = ""   # reasoning text, if the model produced any


@dataclass
class TurnResult:
    ok: bool = False
    error: str = None
    replies: list = field(default_factory=list)
    selected: list = field(default_factory=list)
    selector_note: str = None
    model: str = None
    prompt_tokens: object = "?"
    cached_tokens: object = None
    response_tokens: object = "?"
    total_tokens: object = "?"
    turn_tokens: int = 0
    elapsed: float = 0.0
    memory_due: bool = False
    tokens_state: tuple = None


# A model that sends nothing for this many seconds is skipped for the next one in the chain. Without it, one
# stalled provider held a reply for several minutes. Steady streaming never trips it. Raise it for a slow local model.
ATTEMPT_LIMIT = 90


def _tracked(fn, progress):
    # Wrap a stream callback: each call counts as progress, and it goes silent once the attempt was abandoned.
    if fn is None:
        return None

    def inner(*args):
        if progress["live"]:
            progress["at"] = time.time()
            fn(*args)
    return inner


def _run_with_limit(fn, progress, *args):
    # Run fn(*args) in a worker thread and give up when nothing has arrived for ATTEMPT_LIMIT seconds.
    # Python cannot stop a thread, so an abandoned call finishes in the background, but its output is ignored.
    box = {}
    done = threading.Event()

    def work():
        try:
            box["value"] = fn(*args)
        except BaseException as err:
            box["error"] = err
        finally:
            done.set()

    threading.Thread(target=work, daemon=True).start()
    while not done.wait(1.0):
        if time.time() - progress["at"] > ATTEMPT_LIMIT:
            progress["live"] = False
            raise TimeoutError(f"no answer for {ATTEMPT_LIMIT} seconds")
    if "error" in box:
        raise box["error"]
    return box["value"]


# ---------------------------------------------------------------- engine

def _sandbox_tools():
    declarations = []
    for spec in sandbox.TOOLS:
        props = {key: types.Schema(type="STRING", description=desc) for key, (_, desc) in spec["params"].items()}
        declarations.append(types.FunctionDeclaration(
            name=spec["name"], description=spec["description"],
            parameters=types.Schema(type="OBJECT", properties=props, required=spec["required"])))
    return [types.Tool(function_declarations=declarations)]


def _function_calls(resp):
    calls = getattr(resp, "function_calls", None)
    return list(calls) if calls else []


class Engine:
    # One chat session: a character (or group) plus an optional user persona.

    def __init__(self, client, ai_dir, user_dir=None, log=None, state_dir=None,
                 on_request=None, on_tokens=None, allow_root_user=True,
                 member_memory=True, persist_config=True):
        # state_dir: where memory.json and memory.md live (default: ai_dir).
        # on_request: called once per Gemini call instead of the global tracker.
        # on_tokens: called with a turn's token count; returns (used, limit) like the global tracker.
        # allow_root_user: fall back to ./user.md when no user folder is given.
        # member_memory: let the group director read members' shared memory.md snippets.
        # persist_config: write the last-used model to the shared config.json.
        self.client = client
        self.log = log or (lambda msg: None)
        self.ai_dir = ai_dir
        self.user_dir = user_dir
        self.state_dir = state_dir or ai_dir
        self.on_request = on_request
        self.on_tokens = on_tokens
        self.member_memory = member_memory
        # Admin switches, set by the web app before each request. Memory is on by default.
        self.memory_enabled = True
        self.palace_enabled = True
        self.palace_dir = None   # an account's own palace folder; None means ai_dir
        self.palace_text = ""
        self.persist_config = persist_config
        self.allow_root_user = allow_root_user
        self.character_file = os.path.join(ai_dir, "character.md")
        self.memory_file = os.path.join(self.state_dir, "memory.json")
        self.memory_bank_file = os.path.join(self.state_dir, "memory.md")
        self.pinned_file = os.path.join(self.state_dir, "pinned.md")   # facts the memory refresh never touches
        self.window_override = None        # the user's rolling window size; None = the defaults
        self.temperature_override = None   # the character's creativity; None = the defaults
        self.reply_length_override = None  # the reply length target in tokens, not counting reasoning; None = no target
        self.summary_file = os.path.join(self.state_dir, "summary.json")
        self.summary_text = ""
        self.summary_covered = 0           # how many messages from the start the summary already covers
        self._load_summary()
        self.group_config_file = os.path.join(ai_dir, "group_config.json")
        # per account and character, next to memory.json; only when the character's flag is on
        self.sandbox_root = sandbox.root_for(self.state_dir) if sandbox.is_enabled(ai_dir) else None

        self.fallback_models = []
        self.group_models = []
        self.extra_models = []         # provider model ids added by the server, e.g. 'deepseek:deepseek-flash'
        self.model_name = None
        self.pinned_model = None
        self.thinking = False          # reasoning shown and enabled (off by default)

        self.full_history = []
        self.regen_stash = None        # set while a regenerate runs: the exchange it took out of memory
        self.spicy_to_safe = {}
        self.safe_to_spicy = {}

        self.character_name = display_for(ai_dir)
        self.character_color = "cyan"
        self.character_prompt = ""
        self.is_group = False
        self.group_members = []
        self.member_notes = {}         # per-member notes for an account's own group, keyed by folder name
        self.user_prompt = ""
        self.user_display_name = "You"
        self.user_color = "blue"
        self.system_prompt = ""
        self.created = False

    def _count_request(self, model=None):
        # Count one model call: the caller's hook if set, else the global tracker. The model name lets the
        # hook record which provider's key the call used.
        if self.on_request is not None:
            self.on_request(model or self.model_name)
        else:
            check_and_update_requests()

    def _count_tokens(self, n, model=None):
        if self.on_tokens is not None:
            return self.on_tokens(n, model or self.model_name)
        return check_and_update_tokens(n)

    # ---- setup

    def discover_models(self):
        fetched = []
        try:
            for m in self.client.models.list():
                name = m.name.replace("models/", "")
                if name in ALLOWED_MODELS:
                    fetched.append(name)
        except Exception as e:
            self.log(f"[yellow]Could not fetch dynamic model list: {e}. Using defaults.[/yellow]")

        last_working = DEFAULT_FALLBACKS[0]
        cfg = _read_json(CONFIG_FILE, {})
        if isinstance(cfg, dict):
            last_working = cfg.get("last_model", DEFAULT_FALLBACKS[0])

        models = []
        # the default chain is Gemini only. DeepSeek, local LLM and Claude Code run only when a user picks them
        # (pin_model puts the pick first, and the Gemini chain still follows as a fallback).
        for m in PRIORITY_MODELS + [last_working] + fetched + DEFAULT_FALLBACKS:
            if m and m not in models and m in ALLOWED_MODELS:
                models.append(m)
        self.fallback_models = models
        self.model_name = models[0]
        # group chats skip Gemma (its free tier is too small), but keep every other backend
        self.group_models = [m for m in models if "gemma" not in m.lower()]
        return models

    def create_profile_folder(self):
        # Make a new character folder with a starter character.md and empty memory.json.
        os.makedirs(self.ai_dir, exist_ok=True)
        self.log(f"[yellow]AI profile folder '{self.ai_dir}' not found. Creating it...[/yellow]")
        if os.path.exists("character.md"):
            shutil.copy("character.md", self.character_file)
        else:
            with open(self.character_file, "w", encoding="utf-8") as f:
                f.write("# Character Persona: " + display_for(self.ai_dir) + "\nColour: Cyan\n- **Personality:** Immersive roleplay partner.")
        if not os.path.exists(self.memory_file):
            with open(self.memory_file, "w", encoding="utf-8") as f:
                json.dump([], f)
        self.created = True

    def generate_summary(self):
        # Short intro for a freshly created character. Not counted as a request, as before.
        name = self.character_name
        self.log(f"[dim magenta]🧠 Generating character summary for {name} using {self.model_name}... (owo)[/dim magenta]")
        fallback = f"**{name}**\n\n{self.character_prompt}"
        try:
            summary_prompt = (
                f"You are an expert narrative architect. Read the character profile below and provide a concise, engaging summary of {name}, "
                "detailing their name, appearance, personality, and setting in a rich, immersive narrative overview.\n\n"
                f"Character Profile:\n{self.character_prompt}"
            )
            resp = self.client.models.generate_content(model=self.model_name, contents=summary_prompt)
            text = _text(resp)
            return text.strip() if text else fallback
        except Exception:
            return fallback

    def load_profile(self):
        # Read substitutions, character, user persona, memory bank and history from disk.
        self._load_substitutions()
        self._load_character()
        self._load_user()
        self._build_system_prompt()
        self._load_history()

    def _load_substitutions(self):
        if not os.path.exists(GLOBAL_SUBS_FILE):
            _write_json(GLOBAL_SUBS_FILE, DEFAULT_SUBSTITUTIONS)
        self.spicy_to_safe = {}
        if os.path.exists(GLOBAL_SUBS_FILE):
            try:
                self.spicy_to_safe = json.loads(_read_text(GLOBAL_SUBS_FILE))
                self.log(f"[dim]Loaded {len(self.spicy_to_safe)} global keyword substitutions from {GLOBAL_SUBS_FILE}[/dim]")
            except Exception as e:
                self.log(f"[yellow]Could not load substitutions.json: {e}[/yellow]")
        self.safe_to_spicy = {v.lower(): k for k, v in self.spicy_to_safe.items()}

    def apply_private_group(self, display, members, notes):
        # An account's own group: members and notes come from its saved settings, not a character folder.
        self.is_group = True
        self.group_members = list(members)
        self.member_notes = dict(notes)
        self.member_memory = True
        self.character_name = display
        self.character_color = "magenta"
        self.character_prompt = (f"You are managing a group chat group named {display} consisting of members: "
                                 f"{', '.join(display_for(m) for m in self.group_members)}.")
        self._build_system_prompt()

    def _load_character(self):
        self.character_name = display_for(self.ai_dir)
        self.character_color = "cyan"
        self.character_prompt = ""
        self.is_group = False
        self.group_members = []

        if os.path.exists(self.group_config_file):
            self.is_group = True
            try:
                self.group_members = json.loads(_read_text(self.group_config_file)).get("members", [])
            except Exception:
                pass

        if self.is_group:
            self.character_name = display_for(self.ai_dir)
            self.character_color = "magenta"
            self.character_prompt = (
                f"You are managing a group chat group named {display_for(self.ai_dir)} consisting of members: "
                f"{', '.join(display_for(m) for m in self.group_members)}."
            )
            self.log(f"[bold green]Loaded Group Chat: {_mk(display_for(self.ai_dir))} with members {[display_for(m) for m in self.group_members]} (o_O)[/bold green]")
        elif os.path.exists(self.character_file):
            self.character_prompt = _read_text(self.character_file)
            for line in self.character_prompt.splitlines():
                if "colour:" in line.lower() or "color:" in line.lower():
                    parts = line.split(":")
                    if len(parts) > 1:
                        col = parts[1].strip().lower()
                        if col:
                            self.character_color = get_safe_color(col, "cyan")
                if "character persona:" in line.lower() or line.startswith("# "):
                    if ":" in line:
                        parts = line.split(":", 1)
                        if len(parts) > 1 and len(parts[1].strip()) > 0:
                            self.character_name = parts[1].strip()
                    elif line.startswith("# "):
                        self.character_name = line[2:].strip()
            self.log(f"[bold green]Loaded AI persona from {self.character_file} ({_mk(self.character_name)}) (^V^)[/bold green]")
        else:
            self.character_prompt = "You are a helpful, immersive roleplay partner."

    def _load_user(self):
        self.user_prompt = ""
        self.user_display_name = "You"
        self.user_color = "blue"

        if self.user_dir and os.path.exists(self.user_dir):
            user_char_file = os.path.join(self.user_dir, "character.md")
            user_md_file = os.path.join(self.user_dir, "user.md")
            target = user_char_file if os.path.exists(user_char_file) else (user_md_file if os.path.exists(user_md_file) else None)
            if target and os.path.exists(target):
                self.user_prompt = _read_text(target)
                for line in self.user_prompt.splitlines():
                    if "colour:" in line.lower() or "color:" in line.lower():
                        parts = line.split(":")
                        if len(parts) > 1:
                            col = parts[1].strip().lower()
                            if col:
                                self.user_color = get_safe_color(col, "blue")
                    if "name:" in line.lower() or "persona:" in line.lower() or line.startswith("# "):
                        if ":" in line:
                            parts = line.split(":", 1)
                            if len(parts) > 1 and len(parts[1].strip()) > 0:
                                self.user_display_name = parts[1].strip()
                        elif line.startswith("# "):
                            self.user_display_name = line[2:].strip()
                self.log(f"[bold {self.user_color}]Loaded user persona from folder '{self.user_dir}' ({self.user_display_name}) (O_O)[/bold {self.user_color}]")
        elif self.allow_root_user:
            if os.path.exists("user.md"):
                self.user_prompt = _read_text("user.md")
                self.user_display_name = self.user_dir or "You"
                self.log(f"[bold blue]Loaded root user.md ({self.user_display_name})[/bold blue]")

    def _build_system_prompt(self):
        prompt = self.character_prompt
        prompt += NARRATIVE_INSTRUCTION
        prompt += DIALOGUE_INSTRUCTION
        prompt += FORMAT_INSTRUCTION
        if self.user_prompt:
            prompt += f"\n\n--- User Persona ---\nThe user interacting with you is playing as the following character:\n{self.user_prompt}"

        context_file = os.path.join(self.ai_dir, "context.md")
        if os.path.exists(context_file):
            try:
                ctx = _read_text(context_file).strip()
                if ctx:
                    prompt += ("\n\n--- Context: memories, locations and situations for this character ---\n" + ctx)
            except Exception as e:
                self.log(f"[yellow]Could not load context.md: {e}[/yellow]")

        if self.memory_enabled and os.path.exists(self.memory_bank_file):
            try:
                mb_content = _read_text(self.memory_bank_file)
                if mb_content:
                    prompt += f"\n\n--- Long-Term Memory Bank (memory.md) ---\n{mb_content}"
                self.log(f"[cyan]Loaded memory bank from {self.memory_bank_file} ✨[/cyan]")
            except Exception as e:
                self.log(f"[yellow]Could not load memory.md: {e}[/yellow]")
        if self.memory_enabled and os.path.exists(self.pinned_file):
            try:
                pinned = _read_text(self.pinned_file).strip()
                if pinned:
                    prompt += ("\n\n--- Pinned facts (kept as written; the memory refresh never changes them) ---\n" + pinned)
            except Exception as e:
                self.log(f"[yellow]Could not load pinned.md: {e}[/yellow]")
        if self.summary_text:
            prompt += "\n\n--- Earlier in this chat (running summary) ---\n" + self.summary_text
        # Mind Palace changes most turns, so it sits after the stable parts. Providers cache the
        # unchanged prefix; putting this first would break the cache on every message.
        if self.palace_text:
            prompt += "\n\n--- Mind Palace (matched entries) ---\n" + self.palace_text
        prompt += self.reply_length_clause()
        self.system_prompt = prompt

    def _load_history(self):
        self.full_history = []
        if os.path.exists(self.memory_file):
            try:
                data = json.loads(_read_text(self.memory_file))
                if isinstance(data, list):
                    sanitized = []
                    for item in data:
                        if isinstance(item, dict) and "role" in item and "text" in item:
                            sanitized.append(item)
                        elif isinstance(item, str):
                            sanitized.append({"role": "user", "text": item})
                    self.full_history = sanitized
                self.log(f"[cyan]Loaded full chat history from {self.memory_file} ({len(self.full_history)} total messages recorded) ✨[/cyan]")
            except Exception as e:
                self.log(f"[red]Could not load memory file: {e}. Starting fresh.[/red]")

    # ---- text helpers

    def restore(self, text):
        # Safe words back to the original wording, for display. Whole words only, longest phrase first.
        for safe in sorted(self.safe_to_spicy, key=len, reverse=True):
            spicy = self.safe_to_spicy[safe]
            text = _whole_word(safe).sub(lambda m, s=spicy: s, text)
        return text

    def safe_text(self, text):
        # Original wording to safe words, before text goes to the model. Whole words only, longest first.
        for spicy, safe in sorted(self.spicy_to_safe.items(), key=lambda kv: len(kv[0]), reverse=True):
            text = _whole_word(spicy).sub(lambda m, s=safe: s, text)
        return text

    def prepare_input(self, user_input):
        return self.safe_text(user_input)

    # ---- model selection

    def resolve_model_name(self, raw):
        # Map text like 'gemma-4-31B-it' or '3.8 flash' to a model id.
        # Returns a model id, a list when ambiguous, or None when empty.
        key = raw.strip().lower().replace("models/", "").replace(" ", "-").replace("_", "-")
        if not key:
            return None
        if key[0].isdigit():
            key = "gemini-" + key
        known = {m.lower(): m for m in self.fallback_models}
        for prefix in ("deepseek:", "local:", "claude:"):   # provider ids are exact, never guessed
            if key.startswith(prefix):
                return raw.strip() if raw.strip().lower().startswith(prefix) else key
        if key in known:
            return known[key]
        hits = [m for lm, m in known.items() if lm.startswith(key)] or [m for lm, m in known.items() if key in lm]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            return hits
        return key

    def pin_model(self, model):
        self.pinned_model = model
        self.model_name = model

    def clear_pin(self):
        self.pinned_model = None
        self.model_name = self.fallback_models[0]

    def ordered_models(self, base_list):
        if not self.pinned_model:
            return base_list
        return [self.pinned_model] + [m for m in base_list if m != self.pinned_model]

    # ---- turns

    def _refresh_palace(self, processed):
        # Matched entries depend on the newest messages.
        blocks = []
        if self.palace_enabled:
            recent = [str(m.get("text") or "") for m in self.full_history[-palace.DEPTH_MAX:]]
            entries, cfg = palace.load_all(self.palace_dir or self.ai_dir)
            blocks = palace.match(entries, recent + [str(processed or "")], cfg["budget"], cfg["recursive"])
        # Rebuilt every turn, so an admin switch to memory or the palace takes effect at once.
        self.palace_text = "\n\n".join(blocks)
        self._build_system_prompt()

    def send(self, processed, on_delta=None, on_think=None):
        self._refresh_palace(processed)
        # Run one turn (solo or group). Saves history and tracks tokens on success.
        # on_delta(speaker, text_so_far) is called as each reply streams in, if given.
        # on_think(speaker, thinking_so_far) is called as reasoning streams in (only when thinking is on).
        start = time.time()
        if self.is_group:
            result = self._send_group(processed, on_delta, on_think)
        else:
            result = self._send_solo(processed, on_delta, on_think)
        result.elapsed = time.time() - start
        if not result.ok:
            return result
        result.tokens_state = self._count_tokens(result.turn_tokens)
        self.save()
        result.memory_due = self.memory_enabled and len(self.full_history) > 0 and len(self.full_history) % MEMORY_EVERY == 0
        return result

    def sandbox_active(self):
        return self.sandbox_root is not None and not self.is_group

    def create_chat_session(self, model_name):
        contents = self._window(self.window_size(), labelled=False)
        active = self.sandbox_active()
        return self.client.chats.create(
            model=model_name,
            history=contents if contents else None,
            config=types.GenerateContentConfig(
                system_instruction=self.system_prompt + (SANDBOX_NOTE if active else ""),
                temperature=self.temperature_value(),
                safety_settings=safety_settings(),
                thinking_config=thinking_config_for(model_name, self.thinking),
                tools=_sandbox_tools() if active else None,
            ),
        )

    @staticmethod
    def estimate_tokens(text):
        # Rough token count: about 4 characters per token. An estimate only, roughly +-30%.
        return (len(text or "") + 3) // 4

    def context_estimate(self):
        # Estimate for the next prompt, for the web chat's context marker.
        #
        # The rolling window is the last SOLO_WINDOW messages (GROUP_WINDOW for groups). The message the user is
        # about to send becomes the last one in it, so the window starts at total + 1 - size. This returns that
        # index (window_start), the estimated system prompt, and the stored messages the window will include.
        # Text still in the input box is not known here; the page adds it. Reads only: no model call, no request.
        size = self.window_size()
        total = len(self.full_history)
        start = max(0, total + 1 - size)
        window_tokens = 0
        for e in self.full_history[start:]:
            text = e.get("text", "")
            if self.is_group:
                text = f"[{e.get('speaker', e.get('role', 'user'))}]: {text}"
            window_tokens += self.estimate_tokens(text)
        if self.is_group:
            # each member answers with its own prompt; the largest one is the estimate
            system_tokens = max((self.estimate_tokens(self._member_prompt(m)[0]) for m in self.group_members), default=0)
        else:
            system_tokens = self.estimate_tokens(self.system_prompt)
        return {"window_start": start, "total": total, "window_size": size, "is_group": bool(self.is_group),
                "system_tokens": system_tokens, "window_tokens": window_tokens}

    def _window(self, size, labelled):
        recent = self.full_history[-size:]
        out = []
        for e in recent:
            role = e.get("role", "user")
            if labelled:
                text = f"[{e.get('speaker', role)}]: {e['text']}"
            else:
                text = e.get("text", str(e))
            out.append(types.Content(role=role, parts=[types.Part.from_text(text=text)]))
        return out

    def _call(self, chat, text, speaker, on_delta, on_think=None):
        # Send one message. With on_delta, stream the reply and call on_delta(speaker, text_so_far)
        # as each chunk arrives. With on_think, reasoning streams the same way. Returns an object with
        # .text, .thought and .usage_metadata, like a normal response.
        if self.sandbox_active():
            return self._call_with_tools(chat, text, speaker, on_delta)
        if on_delta is None and on_think is None:
            resp = chat.send_message(text)
            return SimpleNamespace(text=_text(resp), thought=_thought_text(resp) or "",
                                   usage_metadata=getattr(resp, "usage_metadata", None))
        pieces, thoughts, usage = [], [], None
        for chunk in chat.send_message_stream(text):
            piece = _text(chunk)
            if piece:
                pieces.append(piece)
                if on_delta:
                    on_delta(speaker, "".join(pieces))
            thought = _thought_text(chunk)
            if thought and self.thinking:   # reasoning is kept only when the user turned thinking on
                thoughts.append(thought)
                if on_think:
                    on_think(speaker, "".join(thoughts))
            found = getattr(chunk, "usage_metadata", None)
            if found is not None:
                usage = found
        return SimpleNamespace(text="".join(pieces) or None, thought="".join(thoughts),
                               usage_metadata=usage)

    def _call_with_tools(self, chat, message, speaker, on_delta):
        # Run file-tool round trips until the model answers in text. Replies are not streamed here: the whole
        # answer is passed to on_delta once it is finished. The first model call is counted by _send_solo;
        # every later call is counted here, just before it is sent, so each Gemini call counts once.
        totals = {"prompt_token_count": 0, "candidates_token_count": 0, "total_token_count": 0}
        response = None
        for rnd in range(MAX_TOOL_ROUNDS + 1):
            if rnd > 0:
                self._count_request()
            response = chat.send_message(message)
            usage = getattr(response, "usage_metadata", None)
            for key in totals:
                value = _usage(usage, key)
                if isinstance(value, int):
                    totals[key] += value
            calls = _function_calls(response)
            if not calls:
                break
            message = [types.Part.from_function_response(
                           name=c.name,
                           response=sandbox.run_tool(self.sandbox_root, c.name, dict(c.args or {})))
                       for c in calls]
        answer = _text(response) if response is not None else None
        if on_delta and answer:
            on_delta(speaker, answer)
        return SimpleNamespace(text=answer or None, thought="", usage_metadata=SimpleNamespace(**totals))

    def _send_solo(self, processed, on_delta=None, on_think=None):
        result = TurnResult()
        response = None
        for model in self.ordered_models(self.fallback_models):
            if on_delta:
                on_delta(self.character_name, "")  # a failed attempt's partial text is discarded
            if on_think:
                on_think(self.character_name, "")
            progress = {"at": time.time(), "live": True}
            try:
                chat = self.create_chat_session(model)
                response = _run_with_limit(self._call, progress, chat, processed, self.character_name,
                                           _tracked(on_delta, progress), _tracked(on_think, progress))
                if not _text(response):   # an empty answer is a failed attempt: move on, never save it
                    raise RuntimeError("the model sent an empty reply")
                self.model_name = model
                if not self.pinned_model and self.persist_config:
                    _write_json(CONFIG_FILE, {"last_model": model})
                result.ok = True
                break
            except Exception as err:
                progress["live"] = False
                result.error = str(err)

        if not result.ok:
            self._count_request()
            return result

        self._count_request()
        raw = _text(response) or "[Model returned empty response]"

        usage = getattr(response, "usage_metadata", None)
        result.prompt_tokens = _usage(usage, "prompt_token_count")
        result.cached_tokens = _usage(usage, "cached_content_token_count")
        result.response_tokens = _usage(usage, "candidates_token_count")
        result.total_tokens = _usage(usage, "total_token_count")
        result.turn_tokens = result.total_tokens if isinstance(result.total_tokens, int) else 0

        thought = getattr(response, "thought", "") or ""
        self.full_history.append({"role": "user", "text": processed, "ts": _now_iso()})
        self.full_history.append({"role": "model", "text": raw, "ts": _now_iso(), **({"thinking": thought} if thought else {})})

        result.model = self.model_name
        result.replies = [Reply(self.character_name, self.character_color, raw, self.restore(raw),
                                self.model_name, get_request_bar(), thought)]
        return result

    def _send_group(self, processed, on_delta=None, on_think=None):
        result = TurnResult(ok=True)
        selected, note = self._select_members(processed)
        result.selected = selected
        result.selector_note = note

        self.full_history.append({"role": "user", "speaker": "user", "text": processed, "ts": _now_iso()})
        total = 0
        for name in selected:
            label = display_for(name)
            self._count_request()
            prompt, color = self._member_prompt(name)
            contents = self._window(self.window_size(), labelled=True)
            resp = None
            for model in self.ordered_models(self.group_models):
                if on_delta:
                    on_delta(label, "")
                if on_think:
                    on_think(label, "")
                progress = {"at": time.time(), "live": True}
                try:
                    chat = self.client.chats.create(
                        model=model,
                        history=contents if contents else None,
                        config=types.GenerateContentConfig(system_instruction=prompt, temperature=self.temperature_value(),
                                                           thinking_config=thinking_config_for(model, self.thinking)),
                    )
                    resp = _run_with_limit(self._call, progress, chat, f"[{label} respond to the group]: {processed}", label,
                                           _tracked(on_delta, progress), _tracked(on_think, progress))
                    if not _text(resp):
                        raise RuntimeError("the model sent an empty reply")
                    self.model_name = model
                    break
                except Exception as err:
                    progress["live"] = False
                    result.error = str(err)

            text = _text(resp)
            if not text:
                continue
            raw = text.strip()
            thought = getattr(resp, "thought", "") or ""
            result.replies.append(Reply(label, color, raw, self.restore(raw), self.model_name, get_request_bar(), thought))
            self.full_history.append({"role": "model", "speaker": name, "text": raw, "ts": _now_iso(), **({"thinking": thought} if thought else {})})
            usage = getattr(resp, "usage_metadata", None)
            if usage and hasattr(usage, "total_token_count") and usage.total_token_count:
                total += usage.total_token_count

        if not result.replies:
            # nobody answered: drop the user line so history never holds an unanswered turn
            self.full_history.pop()
            result.ok = False
            result.error = result.error or "No group member replied."
            return result

        result.turn_tokens = total
        result.total_tokens = total
        result.prompt_tokens = "N/A"
        result.response_tokens = "N/A"
        result.model = self.model_name
        return result

    def _member_prompt(self, name):
        char_file = os.path.join(name, "character.md")
        prompt = (f"You are {display_for(name)}, participating in a group chat. CRITICAL: Provide a unique response to the user's input. "
                  "Do not repeat what other group members have already said in this turn. Focus on your specific character's perspective.")
        color = "cyan"
        if os.path.exists(char_file):
            content = _read_text(char_file)
            prompt = content + GROUP_RULE
            for line in content.splitlines():
                if "colour:" in line.lower() or "color:" in line.lower():
                    parts = line.split(":")
                    if len(parts) > 1:
                        col = parts[1].strip().lower()
                        if col:
                            color = get_safe_color(col, "cyan")
        note = self.member_notes.get(name)
        if note:
            prompt += "\n\n--- Note from the user for you in this group ---\n" + note
        return prompt + NARRATIVE_INSTRUCTION + DIALOGUE_INSTRUCTION + FORMAT_INSTRUCTION + self.reply_length_clause(), color

    @staticmethod
    def _ordered_mentions(text, labels):
        # Members in the order the director named them. Whole names only, so "Ann" does not match "Anna".
        lowered = text.lower()
        found = []
        for shown_name, folder in labels.items():
            m = re.search(r"(?<!\w)" + re.escape(shown_name.lower()) + r"(?!\w)", lowered)
            if m:
                found.append((m.start(), folder))
        return [folder for _, folder in sorted(found)]

    def _select_members(self, processed):
        # The director reads the whole context and returns who speaks, in what order. It uses the same model
        # list as the members, so a DeepSeek or Claude pick runs the director on that provider too.
        members = self.group_members
        if not members:
            return [], None
        labels = {display_for(m): m for m in members}   # display name -> folder name
        shown = list(labels)

        bios = []
        for name in members:
            snippet = f"Member: {display_for(name)}"
            char_file = os.path.join(name, "character.md")
            mem_file = os.path.join(name, "memory.md")
            if os.path.exists(char_file):
                try:
                    snippet += f"\nPersona snippet:\n{_read_text(char_file)[:500]}"
                except Exception:
                    pass
            if self.member_memory and os.path.exists(mem_file):
                try:
                    snippet += f"\nMemory snippet:\n{_read_text(mem_file)[:300]}"
                except Exception:
                    pass
            if self.member_notes.get(name):
                snippet += f"\nNote from the user about this member:\n{self.member_notes[name]}"
            bios.append(snippet)

        group_memory = ""
        if os.path.exists(self.memory_bank_file):
            try:
                group_memory = _read_text(self.memory_bank_file)[:2000]
            except Exception:
                group_memory = ""
        recent = self.full_history[-SELECTOR_WINDOW:]
        history_text = "\n".join(f"[{display_for(e['speaker']) if 'speaker' in e else e['role']}]: {e['text']}" for e in recent)
        parts = [
            f"You are the director of the group chat '{self.character_name}'.\n",
            f"Available members: {shown}\n\n",
            "Member details, notes and memories:\n", "\n---\n".join(bios), "\n\n",
        ]
        if self.summary_text:
            parts.append(f"Chat summary so far:\n{self.summary_text}\n\n")
        if group_memory:
            parts.append(f"Group memory:\n{group_memory}\n\n")
        parts += [
            f"Recent chat history:\n{history_text}\n\n",
            f"Current user prompt / input: {processed}\n\n",
            f"CRITICAL INSTRUCTION: Decide which members respond to this input and the order they speak in, using the "
            f"user's prompt and all the context above. Choose between 1 and {len(members)} members, only from {shown}. "
            "Leave out any member who should not speak this turn.\n",
            "Return ONLY the chosen names in speaking order, separated by commas (e.g. CharacterB, CharacterA), "
            "with no extra text or explanation.",
        ]
        prompt = "".join(parts)

        # The director answers with the same model the members use: the pinned pick, else the first chain model.
        # A failed call is not retried on another model; the turn falls back to one random member and says why.
        director_model = (self.ordered_models(self.group_models) or [self.model_name])[0]
        selected, failure = [], None
        try:
            self._count_request(director_model)
            chat = self.client.chats.create(model=director_model, config=types.GenerateContentConfig(temperature=0.7))
            text = _text(chat.send_message(prompt))
            if text:
                selected = self._ordered_mentions(text, labels)
            else:
                failure = "empty reply"
        except Exception as err:
            failure = str(err)[:160]
        if not selected:
            selected = [random.choice(members)]
            reason = f" ({failure})" if failure else " (no member names in the reply)"
            return selected, f"Group chat selector fallback with {director_model}{reason}: selected {[display_for(s) for s in selected]} (OwO)"
        return selected, f"Group chat active: director ({director_model}) selected speakers: {[display_for(s) for s in selected]} (OwO)"

    # ---- saving, undo, memory

    def save(self):
        if self.regen_stash is not None:
            # A regenerate has taken the old exchange out of memory. Write nothing until it finishes, so disk keeps
            # the saved exchange; finish_regenerate writes the result, and a failed attempt leaves disk as it was.
            return self.full_history
        _write_history(self.memory_file, self.full_history)
        return self.full_history

    def undo(self):
        # Drop the last turn: its user line and every reply after it (one or several in a group).
        # Returns False when there is no user line to undo.
        last_user = None
        for i in range(len(self.full_history) - 1, -1, -1):
            if self.full_history[i].get("role") == "user":
                last_user = i
                break
        if last_user is None:
            return False
        del self.full_history[last_user:]
        with open(self.memory_file, "w", encoding="utf-8") as f:
            json.dump(self.full_history, f, indent=2, ensure_ascii=False)
        return True

    def _scene_system(self, base):
        # Instructions plus the card and persona, so suggestions and events fit this chat.
        parts = [base]
        if self.character_prompt:
            parts.append("CHARACTER CARD:\n" + self.character_prompt)
        if self.user_prompt:
            parts.append("USER PERSONA:\n" + self.user_prompt)
        return "\n\n".join(parts)

    def _one_shot(self, system, final_prompt):
        # One model call that is not saved to the chat. Counted like any other request. Returns (text, model).
        contents = self._window(self.window_size(), labelled=True)
        models = self.group_models if (self.is_group and self.group_models) else self.fallback_models
        last_err = None
        for model in self.ordered_models(models):
            try:
                self._count_request(model)
                chat = self.client.chats.create(
                    model=model,
                    history=contents if contents else None,
                    config=types.GenerateContentConfig(system_instruction=system, temperature=0.9,
                                                       safety_settings=safety_settings(),
                                                       thinking_config=thinking_config_for(model, self.thinking)),
                )
                text = _text(chat.send_message(final_prompt))
                if text and text.strip():
                    return text.strip(), model
                last_err = RuntimeError("the model sent an empty reply")
            except Exception as err:
                last_err = err
        raise RuntimeError(f"no model could write that ({last_err})")

    def autofill(self):
        # Draft the user's next message from the chat so far. Not saved: the user edits it and sends it.
        text, _ = self._one_shot(self._scene_system(AUTOFILL_SYSTEM), "Write the user's next message now.")
        return _clean_plain(text)

    def roll_event(self):
        # Add one random event to the chat as a narrator note. It is saved, so the next reply sees it.
        text, _ = self._one_shot(self._scene_system(ROLL_SYSTEM), "Write the random event now.")
        text = _clean_plain(text)
        if not text:
            raise RuntimeError("the model sent an empty event")
        self.full_history.append({"role": "user", "speaker": "narrator", "text": "Random event: " + text, "ts": _now_iso()})
        self.save()
        return text

    def _exchange_start(self):
        # Index of the last user line that is not a narrator event, or None.
        for i in range(len(self.full_history) - 1, -1, -1):
            e = self.full_history[i]
            if e.get("role") == "user" and e.get("speaker") != "narrator":
                return i
        return None

    def reply_length_clause(self):
        # The user's reply length target, as a guide for the model. The reply is not cut off at it.
        if self.reply_length_override is None:
            return ""
        tokens = int(self.reply_length_override)
        words = max(1, round(tokens * 0.75))
        return (f"\n\n--- Reply length ---\nAim for each reply to be about {tokens} tokens (roughly {words} words). "
                "This is a target, not a hard limit. Reasoning does not count toward it.")

    def window_size(self):
        # Messages sent to the model: the user's rolling window if set, else the 1:1 or group default.
        if self.window_override:
            return self.window_override
        return GROUP_WINDOW if self.is_group else SOLO_WINDOW

    def temperature_value(self):
        if self.temperature_override is not None:
            return self.temperature_override
        return GROUP_TEMPERATURE if self.is_group else SOLO_TEMPERATURE

    def _load_summary(self):
        data = _read_json(self.summary_file, {})
        if isinstance(data, dict):
            self.summary_text = str(data.get("text") or "").strip()
            try:
                self.summary_covered = max(0, int(data.get("covered", 0)))
            except (TypeError, ValueError):
                self.summary_covered = 0

    def _save_summary(self, text, covered):
        self.summary_text = text
        self.summary_covered = covered
        _write_json(self.summary_file, {"text": text, "covered": covered})

    def edit_message(self, index, text):
        # Replace the wording of one saved message (user line, reply or narrator event). No model call.
        if not 0 <= index < len(self.full_history):
            return False
        stored = self.safe_text(text)
        entry = self.full_history[index]
        entry["text"] = stored
        entry["edited"] = _now_iso()
        i = self._exchange_start()
        if i is not None and index > i and entry.get("role") == "model":
            # keep the saved alternative in step, so swiping back shows the edit
            user = self.full_history[i]
            variants = user.get("variants") or []
            if variants:
                cur = min(max(int(user.get("variant", len(variants) - 1)), 0), len(variants) - 1)
                k = sum(1 for e in self.full_history[i + 1:index] if e.get("role") == "model")
                if k < len(variants[cur]):
                    variants[cur][k]["text"] = stored
        _write_history(self.memory_file, self.full_history)
        return True

    def regen_count(self):
        # Regenerations already made for the last message (the original reply is not counted).
        i = self._exchange_start()
        if i is None:
            return 0
        return max(len(self.full_history[i].get("variants") or []) - 1, 0)

    def begin_regenerate(self):
        # Take the last exchange out of memory and return its user text, for a new turn to answer. Disk is not
        # written here: a failed attempt leaves the saved exchange as it was.
        i = self._exchange_start()
        if i is None:
            return None
        tail = self.full_history[i + 1:]
        if not any(e.get("speaker") != "narrator" for e in tail):
            return None
        user = self.full_history[i]
        replies = [copy.deepcopy(e) for e in tail if e.get("speaker") != "narrator"]
        variants = [copy.deepcopy(v) for v in (user.get("variants") or [])]
        if variants:
            cur = min(max(int(user.get("variant", 0)), 0), len(variants) - 1)
            variants[cur] = replies
        else:
            variants = [replies]
        self.regen_stash = {"user": copy.deepcopy(user), "variants": variants,
                            "narrator": [copy.deepcopy(e) for e in tail if e.get("speaker") == "narrator"]}
        del self.full_history[i:]
        return user.get("text")

    def finish_regenerate(self):
        # After the new turn saved: keep the old replies as an alternative, and put back the narrator events
        # that came after them.
        stash = self.regen_stash
        if stash is None:
            return
        self.regen_stash = None
        i = self._exchange_start()
        if i is None:
            return
        user = self.full_history[i]
        fresh = [copy.deepcopy(e) for e in self.full_history[i + 1:] if e.get("speaker") != "narrator"]
        variants = stash["variants"] + [fresh]
        user["ts"] = stash["user"].get("ts", user.get("ts"))
        user["variants"] = variants
        user["variant"] = len(variants) - 1
        self.full_history.extend(stash["narrator"])
        _write_history(self.memory_file, self.full_history)

    def swipe(self, delta):
        # Show the previous (-1) or next (+1) saved reply to the last exchange. False when there is none.
        i = self._exchange_start()
        if i is None:
            return False
        user = self.full_history[i]
        variants = user.get("variants") or []
        if len(variants) < 2:
            return False
        cur = min(max(int(user.get("variant", len(variants) - 1)), 0), len(variants) - 1)
        new = cur + delta
        if not 0 <= new < len(variants):
            return False
        narrator = [e for e in self.full_history[i + 1:] if e.get("speaker") == "narrator"]
        self.full_history[i + 1:] = [copy.deepcopy(e) for e in variants[new]] + narrator
        user["variant"] = new
        _write_history(self.memory_file, self.full_history)
        return True

    def update_memory_bank(self):
        # Rewrite memory.md from the recent history, trying models in order.
        if not self.full_history:
            self.log("[yellow]No chat history to save yet! (OwO)[/yellow]")
            return

        models = []
        for m in MEMORY_MODELS_HEAD + [self.model_name] + MEMORY_MODELS_TAIL:
            if m and m not in models:
                models.append(m)

        window = self.window_size()
        cutoff = max(0, len(self.full_history) - window)
        start = max(min(self.summary_covered, cutoff), cutoff - SUMMARY_MAX_MESSAGES)
        pending = self.full_history[start:cutoff]        # messages that have left the window since the last summary
        want_summary = bool(pending)
        summary_turns = "\n".join(f"{e.get('speaker') or e.get('role', 'user')}: {e.get('text', '')[:300]}" for e in pending)

        for m in models:
            try:
                self._count_request(m)
                time.sleep(2)
                self.log(f"[dim magenta]🧠 Updating memory.md using {m}... (owo)[/dim magenta]")

                turns = [f"{e['role']}: {e['text'][:300]}" for e in self.full_history[-MEMORY_SNIPPET:]]
                snippet = self.safe_text("\n".join(turns))

                existing = ""
                if os.path.exists(self.memory_bank_file):
                    try:
                        existing = _read_text(self.memory_bank_file).strip()
                    except Exception:
                        pass

                prompt = MEMORY_PROMPT
                if existing:
                    prompt += f"\n\nExisting Memory Bank (memory.md):\n{existing}"
                prompt += f"\n\nNew History Snippet to Analyze:\n{snippet}"
                if want_summary:
                    prompt += (f"\n\nRunning summary so far (earlier in this chat):\n{self.summary_text or '(none yet)'}"
                               f"\n\nMessages that have just left the recent window, oldest first:\n{self.safe_text(summary_turns)}"
                               "\n\nAfter the memory bank, on its own line write === SUMMARY ===, then the updated running summary: "
                               "plain prose under 250 words covering what happened, decisions made and facts now established. "
                               "Keep the points from the existing summary that still matter.")

                chat = self.client.chats.create(
                    model=m,
                    config=types.GenerateContentConfig(temperature=0.7, safety_settings=safety_settings()),
                )
                resp = chat.send_message(prompt)

                usage = getattr(resp, "usage_metadata", None)
                total = usage.total_token_count if usage and hasattr(usage, "total_token_count") else 0
                if isinstance(total, int) and total > 0:
                    self._count_tokens(total, m)

                text = (_text(resp) or "").strip()
                text = re.sub(r"^```markdown\s*", "", text)
                text = re.sub(r"^```\s*", "", text)
                text = re.sub(r"\s*```$", "", text)
                text = text.strip()
                summary_text = None
                if want_summary and "=== SUMMARY ===" in text:
                    text, _, tail = text.partition("=== SUMMARY ===")
                    summary_text = tail.strip()
                    text = text.strip()

                if not text:
                    self.log(f"[yellow]Model {m} returned empty response. Skipping update.[/yellow]")
                    continue

                with open(self.memory_bank_file, "w", encoding="utf-8") as f:
                    f.write(text)
                if summary_text:
                    self._save_summary(summary_text, cutoff)
                self._build_system_prompt()
                self.log("[dim green]✨ Successfully updated memory.md! ✨[/dim green]")
                return
            except Exception as e:
                self.log(f"[yellow]Error with model {m}: {e}[/yellow]")
                continue

        if not os.path.exists(self.memory_bank_file):
            with open(self.memory_bank_file, "w", encoding="utf-8") as f:
                f.write("# Memory Bank\n\n## Relationships\nActive roleplay partnership.\n\n## Key Facts\n- Session is active and saved.\n")
            self.log("[green]✨ Created baseline memory.md successfully! ✨[/green]")
