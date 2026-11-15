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
import threading
import datetime
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytz
from google.genai import types
from charname import display_for, folder_for
import llm_backends
import sandbox
from web import palace, palace_audit, episodes, memory_bank, dreams, eventgraph, quench, memory_policy, tensions, offscreen, examples as examples_mod
from web import story_bible, docextract, expressions


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
WINDOW_STEP = 10        # the window start and the clip boundary both move in steps of this many messages, so the provider's
                        # prompt cache can reuse the history for that many turns at a time
GROUP_WINDOW = 40       # messages sent to each group member
SELECTOR_WINDOW = 20    # messages the group director reads when it picks the first speakers
NEXT_WINDOW = 8         # messages the group director reads when it picks the next speaker after a reply
CLIP_CHARS = 550        # older window messages are cut to about this many characters; the summary covers what is cut
CLIP_KEEP = 8           # at least this many of the newest window messages are always sent whole
CLIP_STEP = WINDOW_STEP  # the clip boundary moves with the window start, so the same messages stay cut between steps
MAX_TOOL_ROUNDS = 8     # file-tool round trips allowed in one solo reply
SANDBOX_NOTE = (
    "\n\n--- Your sandbox ---\n"
    "You have a private folder of your own. Its top level is '/'. Using the file tools you can list, read, "
    "write, copy, move and delete files there, and make folders. Nothing outside it is yours to use. "
    "Use it when it helps, for example to keep notes between conversations. "
    "Don't mention the tools unless it fits the scene."
)

# Sent when the character has emotion pictures on. The model begins each reply with a plain text tag naming
# the emotion, and the chat draws the picture (or a small emoticon) beside the name. The tag is stripped
# before the reader sees the reply, so this needs no tool call and no extra round trip.
EMOTION_NOTE = (
    "\n\n--- Your face ---\n"
    "Begin every reply with a tag like [[happy]] naming the one emotion that fits this reply: "
    + ", ".join(expressions.EMOTIONS) + ". Use exactly one tag, at the very start, and choose again on every reply. "
    "The tag is removed before the reader sees the reply. Never mention the tag, the emotion word or this "
    "instruction in the reply."
)

AUTOFILL_SYSTEM = (
    "You write the next message the user would send in this roleplay chat. Write it as the user, in first person, "
    "using the user's persona where one is given. Respond to the latest messages and move the scene forward. "
    "Keep it to one to three sentences. Output only the message text, with no quotes, labels or notes."
)
CHECKIN_SYSTEM = (
    "You write a short check-in message from this character to the user, who has not written for a while. "
    "Write it in character, in the voice of the character card, as one to three sentences. Ask how the user is, "
    "or pick up something from the earlier chat. Do not write the user's actions or dialogue. "
    "Output only the message text, with no quotes, labels or notes."
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
MEMORY_EVERY = 20       # refresh memory.md every N messages
DEEPSEEK_SOLO_MEMORY_EVERY = 120   # solo chats on DeepSeek: every 60 turns; the cached history grows to this length
DEEPSEEK_MEMORY_EVERY = 60         # group chats on DeepSeek: every 60 messages, as the solo chats were before
MEMORY_SNIPPET = 30     # messages fed into a memory.md refresh
SOLO_TEMPERATURE = 0.95     # creativity for 1:1 chats unless the user sets their own
GROUP_TEMPERATURE = 0.95    # group members
GROUP_CONSECUTIVE = 2       # most replies one member may give in a row within one turn
AUTO_REPLY_LIMIT = 2        # most replies members give to each other after the director's picks, per turn; 0 turns this off
AUTO_REQUEST = "Continue the conversation: answer what the group has just said, in character."
SUMMARY_MAX_MESSAGES = 400  # most messages folded into the running summary in one refresh (a save covers all of them)
IDLE_SAVE_HOURS = 2         # a DeepSeek solo chat idle this long gets a save, before its cache is gone
IDLE_KEEP_MESSAGES = 4      # after an idle save the next message starts from the last two turns (four messages)
MAX_REGENERATIONS = 30  # regenerations allowed per message; the original reply is kept too, so 31 replies at most

# Every model id the app may use. Older Gemini ids are retired and are dropped from the chain, the picker and any saved
# last model. Flash is 3.8 only; flash-lite is 3.5 and 3.1; Gemma 4 31B is unchanged. Add an id here to bring it back.
ALLOWED_MODELS = ["gemini-3.8-flash", "gemini-3.5-flash-lite", "gemma-4-31b-it", "gemini-3.1-flash-lite"]
DEFAULT_FALLBACKS = [
    "gemini-3.8-flash",
    "gemini-3.5-flash-lite",
    "gemma-4-31b-it",
    "gemini-3.1-flash-lite",
]
PRIORITY_MODELS = ["gemini-3.8-flash", "gemini-3.5-flash-lite"]
MEMORY_MODELS_HEAD = ["gemini-3.8-flash", "gemini-3.5-flash-lite"]
MEMORY_MODELS_TAIL = ["gemma-4-31b-it", "gemini-3.1-flash-lite"]

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
# A live voice call carries only what the character says. Nothing outside quotation marks is spoken.
VOICE_CALL_INSTRUCTION = (
    "[This turn is a live voice call. Reply only with what you say out loud, every line inside double "
    "quotation marks. Do not write narration, actions, stage directions, thoughts, descriptions, or the "
    "sound of your voice. Do not mention the call itself. Keep each reply short and natural to speak.]"
)
# The rule for the notes that come with a user's message. It sits in the cached system prompt, so each turn's notes
# can be short: they carry the facts and leave the rules here.
NOTES_GUIDE = (
    "\n\n--- Notes in your messages ---\n"
    "Some of the user's messages begin with notes from the simulation and memory, marked as not written by the user. "
    "Act on them naturally. Never quote them, and never discuss their numbers."
)
# A novel's narrator writes prose, not chat turns. It voices the cast from their cards and never writes speaker labels.
# Sent to a novel's narrator when some of the cast have emotion pictures. The tag sits on the spoken line
# itself, so the chat can draw that character's face beside it. Only tagged names get a face.
def _novel_face_note(names):
    return (
        "\n\n--- Faces in the cast ---\n"
        "Some of the cast have a picture for each emotion. When one of them speaks, begin that line with a tag "
        "in exactly this form, then the dialogue on the same line:\n"
        '[[Name|emotion]] "what they say"\n'
        "Name is one of: " + ", ".join(names) + ". emotion is one of: " + ", ".join(expressions.EMOTIONS) + ". "
        "Use one tag per spoken line and put the tag before the quotation marks. Leave narration and the other "
        "characters' lines untagged. Never mention the tags, the emotions or this instruction."
    )


NOVEL_INSTRUCTION = (
    "\n\nYou are the narrator of a novel. Write in prose, third person and past tense, the way a novelist does: scene, "
    "action and dialogue woven into the narration, with quotation marks around speech. Voice each character in the cast "
    "as their card describes them. Never write a speaker label such as 'Name:'. Keep each passage to a few paragraphs. "
    "The user writes their own lines or direction; answer it inside the story, and do not step out of the narration to "
    "talk to the user."
)
NOVEL_CARD_MAX = 1500   # characters of each cast member's card that the narrator sees
# The save's last message when it shares the chat's cached prefix: the chat is above it, and the memory bank is in the
# system prompt, so the save sends only these instructions and the messages that have left the prompt.
SAVE_SUMMARY_REQUEST = (
    "\n\nAfter the memory changes, on its own line write === SUMMARY ===, then the updated running summary: "
    "plain prose under 250 words covering what happened, decisions made and facts now established. "
    "Keep the points from the existing summary that still matter.")
MEMORY_CHANGES_PROMPT = (
    "You keep a roleplay character's memory bank (memory.md) up to date. You are given the existing memory bank "
    "and the latest messages. Reply with changes only, one per line, in exactly these forms:\n"
    "ADD: [Section] one new fact, written as one line. Section is one of: Locations, Relationships, "
    "Major Plot Points, Key Facts, Secrets / Hidden Motivations.\n"
    "UPDATE: the exact existing line => the corrected line\n"
    "REMOVE: the exact existing line, only when the chat shows it is no longer true\n"
    "Track every location visited or mentioned, with its state and events. Add only what is new and lasting. "
    "Do not repeat facts already in the bank. If nothing has changed, reply NONE. Write nothing else."
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
REASONING_LEVELS = ("low", "medium", "high")


def thinking_config_for(model, enabled, reasoning="low"):
    # Thinking settings for one model. Off by default.
    #
    # OpenAI-style and Claude Code backends read include_thoughts and the thinking level. Gemma accepts no
    # thinking config at all. Gemini models that refuse a budget of 0 get include_thoughts=False with no
    # budget, so any thinking they do stays hidden.
    level = str(reasoning or "low").lower()
    if level not in REASONING_LEVELS:
        level = "low"
    thinking_levels = getattr(types, "ThinkingLevel", None)
    thinking_level = getattr(thinking_levels, level.upper(), None) if thinking_levels is not None else None
    if model.startswith(PROVIDER_PREFIXES):
        if not enabled:
            return types.ThinkingConfig(include_thoughts=False)
        return types.ThinkingConfig(include_thoughts=True, thinking_level=thinking_level)
    if "gemma" in model:
        return None
    if enabled:
        return types.ThinkingConfig(include_thoughts=True, thinking_level=thinking_level)
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


EMOTION_TAG = re.compile(r"^\s*\[\[\s*(?:[^\]|\n]+\|)?\s*([a-z]+)\s*\]\]\s*", re.I)


def take_emotion_tag(text):
    # A reply may start with [[happy]] (solo or group) or [[Name|happy]] (a novel's cast). Strip that
    # tag and return the known emotion, or leave the text alone when the tag is not one of ours.
    match = EMOTION_TAG.match(text or "")
    if not match:
        return text, ""
    emotion = expressions.clean_emotion(match.group(1))
    if not emotion:
        return text, ""
    return (text[match.end():] or ""), emotion


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
    emotion: str = ""    # the emotion the model set for this reply, or ""


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

    def usage_detail(self):
        # The token split for the daily stats: prompt (input), the cached part of it, output, and the
        # seconds the turn took. Unknown figures stay 0, so a provider that reports nothing is not guessed.
        def number(value):
            return int(value) if isinstance(value, int) else 0
        prompt = number(self.prompt_tokens)
        cached = min(number(self.cached_tokens), prompt) if prompt else 0
        output = number(self.response_tokens)
        return {"input_tokens": prompt, "cached_tokens": cached, "output_tokens": output,
                "seconds": float(self.elapsed or 0.0)}


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
    return declarations


def _tools(sandbox_tools=False):
    # The tool list for one call, or None. A call with no tools is a plain reply. Emotion pictures are
    # a prompt tag now, not a tool, so only the sandbox contributes declarations here.
    declarations = _sandbox_tools() if sandbox_tools else []
    return [types.Tool(function_declarations=declarations)] if declarations else None


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
        # on_tokens: called with a turn's token count, the model, and its input/cached/output/seconds split;
        # returns (used, limit) like the global tracker.
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
        self.palace_fired = []   # palace keys whose entries matched this turn, read by the server for the usage counts
        self.palace_text = ""
        self.palace_static = ""  # the constant Mind Palace entries of a solo chat; they sit in the system prompt
        self.mood_text = ""      # the character's simulated state for this turn, set by the server; empty when off
        self.mood_stable = ""    # the mood's emotion and bands, in the system prompt; changes rarely
        self.attitude_text = ""  # the relationship changes this turn, set by the server; empty when none
        self.attitude_stable = ""  # the relationship level and its tone, in the system prompt; changes rarely
        self.offscreen_text = "" # what the character did between sessions, set by the server; empty when off
        self.persist_config = persist_config
        self.allow_root_user = allow_root_user
        self.character_file = os.path.join(ai_dir, "character.md")
        self.memory_file = os.path.join(self.state_dir, "memory.json")
        self.memory_bank_file = os.path.join(self.state_dir, "memory.md")
        self.pinned_file = os.path.join(self.state_dir, "pinned.md")   # facts the memory refresh never touches
        self.window_override = None        # the user's rolling window size; None = the defaults
        self.quench_enabled = False        # this account's near-lossless token quenching; set by the server
        self.clip_enabled = True           # this account's clipping of older window messages; set by the server
        self._quench_memo = None           # the quenched older part of the window, kept while its messages do not change
        self._sent_texts = []              # the window texts in the last prompt sent, for the next-prompt estimate
        self._sent_system = None           # the system text that prompt used
        self.last_quench = None            # the last quenching report, for the context marker
        self.temperature_override = None   # the character's creativity; None = the defaults
        self.reply_length_override = None  # the reply length target in tokens, not counting reasoning; None = no target
        self.summary_file = os.path.join(self.state_dir, "summary.json")
        self.summary_text = ""
        self.summary_covered = 0           # how many messages from the start the summary already covers
        self._load_summary()
        self.episodes_file = os.path.join(self.state_dir, "episodes.json")
        self.episodes = []                 # episodic memory: messages that left the window, cut into short episodes
        self.episodes_covered = 0          # how many messages from the start are already in an episode
        self.episodes_enabled = True       # the admin switch; the character's own switch is read each turn
        self.episodes_on = True            # the switch in effect for this turn: admin switch and the palace setting
        self.episode_text = ""
        self.episode_vectors = {}          # query text hash -> vector, for the few turns that need a similarity search
        self.dream_file = os.path.join(self.state_dir, "dream.json")
        self.dream_calls = 0               # model calls the last dream made, for the server's dream cap
        self.bible_file = os.path.join(self.state_dir, story_bible.FILE)   # the chat's story bible, read only
        self.bible_calls = 0               # model calls the last story bible generation made
        last = _read_json(self.dream_file, {})
        self.dream_len = int(last.get("len", 0)) if isinstance(last, dict) else 0   # history length at the last dream
        self.event_memory_enabled = False  # the admin switch in effect this turn; set by the server
        self.event_text = ""               # the event block for this turn; empty when off or nothing matches
        self.graph_file = os.path.join(self.state_dir, eventgraph.FILE)
        self.graph = eventgraph.load(self.graph_file)   # the event memory graph, written by the nightly dream
        self.tensions_file = os.path.join(self.state_dir, tensions.FILE)
        self.tensions = tensions.load(self.tensions_file)   # open narrative tensions, read into each reply's notes
        self.tension_mode = tensions.DEFAULT_MODE           # off, dream or save; set by the server from the chat's setting
        self.call_mode = False                              # a live voice call: only spoken lines, set for one turn
        self.world_text = ""                                # the world block for this chat; empty when it has none
        self.is_novel = False                               # a novel: a narrator writes prose for a cast
        self.novel_faces = []                               # cast members whose spoken lines carry an emotion tag
        self._load_episodes()
        # Learned memory retention: outcome-weighted scores for the memories the character extracted for itself.
        self.policy_enabled = False                  # the admin switch; set by the server before each turn
        self.policy_file = os.path.join(self.state_dir, memory_policy.FILE)
        self.policy = memory_policy.load(self.policy_file)
        self.policy_retrieved = []                   # (key, kind, title) that went into the prompt this turn
        self.policy_forgotten = []                   # titles the last forgetting pass removed
        self.group_config_file = os.path.join(ai_dir, "group_config.json")
        # per account and character, next to memory.json; only when the character's flag is on
        self.sandbox_root = sandbox.root_for(self.state_dir) if sandbox.is_enabled(ai_dir) else None

        self.fallback_models = []
        self.group_models = []
        self.extra_models = []         # provider model ids added by the server, e.g. 'deepseek:deepseek-flash'
        self.model_name = None
        self.pinned_model = None
        self.thinking = False          # reasoning shown and enabled (off by default)
        self.reasoning = "low"         # reasoning level when thinking is on: low, medium or high
        self.reasoning_levels = list(REASONING_LEVELS)   # levels this account's tier may pick

        self.full_history = []
        self.regen_stash = None        # set while a regenerate runs: the exchange it took out of memory
        self.spicy_to_safe = {}
        self.safe_to_spicy = {}
        self._safe_pairs = []          # (spicy, safe), longest word first, set by _load_substitutions
        self._spicy_pairs = []         # (safe, spicy), longest word first

        self.character_name = display_for(ai_dir)
        self.character_color = "cyan"
        self.character_prompt = ""
        self.example_text = ""        # the character's own example messages, as a prompt block; empty when it has none
        self.author_note = ""         # an optional instruction for the chat, kept in the cached system prompt
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

    def _count_tokens(self, n, model=None, detail=None):
        if self.on_tokens is not None:
            return self.on_tokens(n, model or self.model_name, detail)
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
        # The word pairs in one order each, longest word first, so a short word does not get applied inside a
        # longer phrase. restore and safe_text run on every window message every turn, so the order is
        # precomputed once here instead of a fresh sort on each call.
        self._safe_pairs = sorted(self.spicy_to_safe.items(), key=lambda kv: len(kv[0]), reverse=True)
        self._spicy_pairs = [(safe, spicy) for safe, spicy in self.safe_to_spicy.items()]
        self._spicy_pairs.sort(key=lambda kv: len(kv[0]), reverse=True)

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

    # A novel: a narrator voices a cast of solo characters, in prose. cast is a list of (display name, card text). The
    # chat stays a solo turn, so the reply is one passage of prose, saved with the narrator's name.
    def apply_novel(self, display, cast):
        self.is_novel = True
        self.is_group = False
        self.group_members = []
        self.character_name = display
        self.character_color = "magenta"
        cards = [f"--- {name} ---\n{card.strip()[:NOVEL_CARD_MAX]}" for name, card in cast]
        self.character_prompt = f"The novel is named {display}. The cast:\n\n" + "\n\n".join(cards)
        # Cast members with emotion pictures: their spoken lines get a small tag the chat draws a face from.
        self.novel_faces = []
        for name, _card in cast:
            try:
                if expressions.state(folder_for(name))["emotions"]:
                    self.novel_faces.append(name)
            except OSError:
                continue
        self._build_system_prompt()

    # The world this chat is set in, as a block for the system prompt. Rebuilt only when it changes.
    def set_world(self, text):
        text = text or ""
        if text != self.world_text:
            self.world_text = text
            self._build_system_prompt()

    def _load_character(self):
        self.character_name = display_for(self.ai_dir)
        self.character_color = "cyan"
        self.character_prompt = ""
        self.example_text = ""
        self.author_note = ""
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
        # Example messages live beside the card. They show the voice, so they sit right after it in the prompt.
        if not self.is_group:
            self.example_text = examples_mod.prompt_block(examples_mod.load(self.ai_dir), self.character_name)
        # The author's note is set by the character editor. It is not part of the card, and it goes in the cached
        # system prompt after the card and memory, so it steers tone and pacing without rewriting the card.
        note_file = os.path.join(self.ai_dir, "author_note.md")
        if os.path.exists(note_file):
            try:
                self.author_note = _read_text(note_file).strip()
            except Exception as e:
                self.log(f"[yellow]Could not load author_note.md: {e}[/yellow]")

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

    # The system prompt, ordered from the least to the most often changed part, so that the provider's prompt cache
    # reuses the longest possible prefix. Nothing that changes every turn is here: that goes in the newest user message
    # (see _turn_notes). Changes are: context.md and pinned facts when the user edits them, the memory bank when a
    # refresh or a dream runs, and the summary when the window moves.
    def _build_system_prompt(self):
        prompt = self.character_prompt
        if self.example_text:
            prompt += "\n\n" + self.example_text
        # The world sits after the examples: it changes only when the chat's world is changed, not every turn.
        if self.world_text:
            prompt += "\n\n--- The world this story is set in ---\n" + self.world_text
        if self.is_novel:
            prompt += NOVEL_INSTRUCTION
            if self.novel_faces:
                prompt += _novel_face_note(self.novel_faces)
        else:
            prompt += NARRATIVE_INSTRUCTION
            prompt += DIALOGUE_INSTRUCTION
            prompt += FORMAT_INSTRUCTION
        prompt += self.reply_length_clause()
        prompt += NOTES_GUIDE
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

        if self.memory_enabled and os.path.exists(self.pinned_file):
            try:
                pinned = _read_text(self.pinned_file).strip()
                if pinned:
                    prompt += ("\n\n--- Pinned facts (kept as written; the memory refresh never changes them) ---\n" + pinned)
            except Exception as e:
                self.log(f"[yellow]Could not load pinned.md: {e}[/yellow]")
        if self.memory_enabled and os.path.exists(self.memory_bank_file):
            try:
                mb_content = _read_text(self.memory_bank_file)
                if mb_content:
                    prompt += f"\n\n--- Long-Term Memory Bank (memory.md) ---\n{mb_content}"
                self.log(f"[cyan]Loaded memory bank from {self.memory_bank_file}[/cyan]")
            except Exception as e:
                self.log(f"[yellow]Could not load memory.md: {e}[/yellow]")
        # The constant Mind Palace entries and the open tensions change only when the palace or the threads change, so
        # they sit here, in the cached part, rather than in each turn's notes.
        if self.palace_static:
            prompt += "\n\n--- Mind Palace (always on) ---\n" + self.palace_static
        tension_block = self._tension_block()
        if tension_block:
            prompt += "\n\n" + tension_block
        # The off-screen record of a solo chat is the same for the whole session. Its block already starts with blank
        # lines, so it is added as it is. Empty when the chat's switch is off.
        if self.offscreen_text and not self.is_group:
            prompt += self.offscreen_text
        # The relationship level and its tone sit here too, so they reach the model on every turn without being resent.
        if self.attitude_stable and not self.is_group:
            prompt += "\n\n" + self.attitude_stable
        if self.mood_stable and not self.is_group:
            prompt += "\n\n" + self.mood_stable
        # The author's note is stable for the whole chat, so it sits in the cached part of the prompt rather
        # than being resent with every user message. It goes after the card and the memory, and before the
        # running summary, which is the part of this block that changes most often.
        if self.author_note:
            prompt += ("\n\n--- Author's note for this chat (follow it when it fits the scene) ---\n"
                       + self.author_note)
        if self.summary_text:
            prompt += "\n\n--- Earlier in this chat (running summary) ---\n" + self.summary_text
        self.system_prompt = prompt

    # The notes for one turn: the relationship, the mood, and the palace, episode and event matches. They are sent with
    # the newest user message, not in the system prompt, so the system prompt and the history before that message stay
    # the same from one turn to the next. The text is kept out of the saved chat. The off-screen record is not here: it
    # is the same for the whole session, so it sits in the system prompt (see _build_system_prompt).
    def _turn_notes(self):
        body = (self.attitude_text + self.mood_text + self._recall_text()).strip()
        if body:
            body = "[Notes for this reply, not written by the user.]\n" + body
        if self.call_mode:
            if body:
                body += "\n\n"
            body += VOICE_CALL_INSTRUCTION
        if not body:
            return ""
        return body + "\n\n[The user says:]\n"

    # The open tensions as constraints for this reply, when the chat tracks them. Empty when off or none are open.
    # The open tensions of a solo chat. They sit in the system prompt, which changes only when a tension pass, a dream or
    # a hand edit changes them, so the cached part of the prompt keeps them between those changes.
    def _tension_block(self):
        if self.tension_mode == "off" or self.is_group:
            return ""
        return tensions.prompt_block(self.tensions)

    # What the model is sent for the user's message: the turn's notes, then the message itself.
    def _turn_message(self, processed):
        return self._turn_notes() + processed

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
                self.log(f"[cyan]Loaded full chat history from {self.memory_file} ({len(self.full_history)} total messages recorded)[/cyan]")
            except Exception as e:
                self.log(f"[red]Could not load memory file: {e}. Starting fresh.[/red]")

    # ---- text helpers

    def restore(self, text):
        # Safe words back to the original wording, for display. Whole words only, longest phrase first.
        for safe, spicy in self._spicy_pairs:
            text = _whole_word(safe).sub(lambda m, s=spicy: s, text)
        return text

    def safe_text(self, text):
        # Original wording to safe words, before text goes to the model. Whole words only, longest first.
        for spicy, safe in self._safe_pairs:
            text = _whole_word(spicy).sub(lambda m, s=safe: s, text)
        return text

    def _recall_text(self):
        # Mind Palace matches and episodic memory for this turn, as one prompt block. Solo and group prompts share it.
        out = ""
        if self.palace_text:
            out += "\n\n--- Mind Palace (matched entries) ---\n" + self.palace_text
        if self.episode_text:
            out += "\n\n--- Earlier moments that match this conversation (episodic memory) ---\n" + self.episode_text
        if self.event_text:
            out += "\n\n" + self.event_text
        return out

    def prepare_input(self, user_input):
        return self.safe_text(user_input)

    # ---- model selection

    # The ordered model chain for one background generation: a dream, a tension pass, a story bible or a document
    # import. It is the fixed Gemini chain in a chat on a provider with its own prompt cache, and the chat's own model
    # first everywhere else (see _background_models). Every memory-job method iterates this once.
    def _memory_models(self):
        return self._background_models()

    # The models for background work. A chat on DeepSeek or OpenRouter keeps those calls on the fixed Gemini chain,
    # because none of them sends a prompt the chat has just sent, so a DeepSeek call would pay the cache-miss rate for
    # tokens no cache holds. Any other chat keeps its own model first, as before. Duplicates are removed.
    def _background_models(self):
        chat_models = self.group_models if (self.is_group and self.group_models) else self.fallback_models
        chat_first = (self.ordered_models(chat_models) or [self.model_name])[0]
        if str(chat_first or "").startswith(("deepseek:", "openrouter:")):
            order = MEMORY_MODELS_HEAD + MEMORY_MODELS_TAIL
        else:
            order = [chat_first] + MEMORY_MODELS_HEAD + [self.model_name] + MEMORY_MODELS_TAIL
        models = []
        for m in order:
            if m and m not in models:
                models.append(m)
        return models

    # The models for a memory save. The save is the one background job that sends the chat's own history, so in an
    # anchored chat the model that answered last comes first: the prefix it just sent is still cached there, and the
    # save then costs the cache-hit rate instead of a cold miss on another model. The background chain follows it.
    def _save_models(self):
        order = self._background_models()
        last = str(self.model_name or "")
        if self.cache_anchored() and last.startswith("deepseek:"):
            order = [last] + order
        models = []
        for m in order:
            if m and m not in models:
                models.append(m)
        return models

    def resolve_model_name(self, raw):
        # Map text like 'gemma-4-31B-it' or '3.8 flash' to a model id.
        # Returns a model id, a list when ambiguous, or None when empty.
        key = raw.strip().lower().replace("models/", "").replace(" ", "-").replace("_", "-")
        if not key:
            return None
        if key[0].isdigit():
            key = "gemini-" + key
        known = {m.lower(): m for m in self.fallback_models}
        for prefix in ("deepseek:", "local:", "claude:", "openrouter:"):   # provider ids are exact, never guessed
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
        self.policy_retrieved = []
        self.palace_fired = []
        self.palace_static = ""
        pcfg = None      # the palace settings, read once and handed to the episode refresh below
        if self.palace_enabled:
            recent = [str(m.get("text") or "") for m in self.full_history[-palace.DEPTH_MAX:]]
            entries, cfg = palace.load_all(self.palace_dir or self.ai_dir)
            pcfg = cfg
            # Only the entries the character wrote for itself are scored and weighted. The user's entries are
            # matched exactly as before, and are never ranked by a score or dropped.
            weights = None
            auto = [e for e in entries if e.get("auto")]
            if self.policy_enabled:
                weights = {palace.palace_key(e): memory_policy.rank_multiplier(self.policy, palace.palace_key(e))
                           for e in auto}
            fired = []
            kept = palace.match_entries(entries, recent + [str(processed or "")], cfg["budget"], cfg["recursive"],
                                        weights, fired=fired)
            self.palace_fired = [palace.palace_key(e) for e in fired]
            # A solo chat's constant entries never change between turns, so they go in the cached system prompt. A group
            # reads the palace from its notes as before. The matching and the budget are the same either way.
            matched_all = list(kept)
            static = [] if self.is_group else [e for e in kept if e.get("constant")]
            static_ids = {id(e) for e in static}
            self.palace_static = "\n\n".join(palace.block_for(e) for e in static)
            kept = [e for e in kept if id(e) not in static_ids]
            blocks = [palace.block_for(e) for e in kept]
            if self.policy_enabled:
                # An entry that did not match this turn is still on the books, so one that never matches can be
                # forgotten for being no use rather than for being old.
                matched = {palace.palace_key(e) for e in matched_all}
                for e in auto:
                    key = palace.palace_key(e)
                    title = e["title"] or e["content"][:60]
                    memory_policy.seen(self.policy, key, "palace", title)
                    if key in matched:
                        self.policy_retrieved.append((key, "palace", title))
        # Rebuilt every turn, so an admin switch to memory or the palace takes effect at once.
        self.palace_text = "\n\n".join(blocks)
        self._refresh_episodes(processed, pcfg)
        self._refresh_events(processed)
        self._build_system_prompt()

    def _refresh_events(self, processed):
        # Event memory for this turn: the events that bear on the latest messages, and how the character has changed.
        # The evolving lines show every turn, because they are the character's current outlook, not a match.
        self.event_text = ""
        if not self.event_memory_enabled or not (self.graph["nodes"] or self.graph["evolving"]):
            return
        texts = [str(m.get("text") or "") for m in self.full_history[-6:]] + [str(processed or "")]
        lines, evolving = eventgraph.recall(self.graph, texts)
        parts = []
        if lines:
            parts.append("--- Events that bear on this moment (memory graph) ---\n" + "\n".join(lines))
        if evolving:
            parts.append("--- How you have changed, from what has happened to you ---\n"
                         + "\n".join(f"- {t}" for t in evolving))
        self.event_text = "\n\n".join(parts)

    def _refresh_episodes(self, processed, pcfg=None):
        # Episodic memory for this turn. Keywords first, which costs nothing. Only when they find nothing, and some
        # episodes have vectors, is the new message embedded and matched by similarity. pcfg is the palace settings
        # already read by _refresh_palace; without it, they are read here (the palace is switched off, so its
        # own matching never loaded them).
        if pcfg is None:
            _, pcfg = palace.load_all(self.palace_dir or self.ai_dir)
        self.episodes_on = self.episodes_enabled and pcfg["episodes"]
        self.episode_text = ""
        if not (self.episodes_on and self.episodes):
            return
        visible_from = self.summary_cut(len(self.full_history))
        texts = [str(m.get("text") or "") for m in self.full_history[-episodes.SCAN_MESSAGES:]] + [str(processed or "")]
        weights = None
        if self.policy_enabled:
            weights = {episodes.key_for(ep): memory_policy.rank_multiplier(self.policy, episodes.key_for(ep))
                       for ep in self.episodes}
        found = episodes.recall_keywords(self.episodes, texts, visible_from, weights=weights, with_items=True)
        if not found and str(processed or "").strip() and any(e.get("vec") for e in self.episodes):
            query = self._query_vector(str(processed))
            if query:
                found = episodes.recall_vectors(self.episodes, query, visible_from, weights=weights, with_items=True)
        if self.policy_enabled:
            self.policy_retrieved += [(episodes.key_for(ep), "episode", str(ep.get("text") or "")[:60])
                                      for ep in found]
        self.episode_text = "\n\n".join(ep["text"] for ep in found)

    def _query_vector(self, text):
        # One embedding for the message being sent, reused if the same text is asked again. None when it fails.
        key = episodes.query_key(text)
        if key in self.episode_vectors:
            return self.episode_vectors[key]
        vectors = episodes.embed(self.client, [text])
        if vectors is None:
            return None
        self._count_request(episodes.EMBED_MODEL)
        self._count_tokens(episodes.estimate_tokens([text]), episodes.EMBED_MODEL)
        if len(self.episode_vectors) >= 32:
            self.episode_vectors = {}
        self.episode_vectors[key] = vectors[0]
        return vectors[0]

    def send(self, processed, on_delta=None, on_think=None):
        # The user's new message is also the outcome of the last turn's memories, so it is scored before this turn
        # decides what to recall. Only then is the new turn's own retrieval recorded.
        if self.policy_enabled:
            memory_policy.start_turn(self.policy)
            memory_policy.settle(self.policy, processed)
        self.model_switch_save()
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
        self._count_tokens(result.turn_tokens, detail=result.usage_detail())
        self.save()
        if self.policy_enabled:
            # The memories that went into this turn's prompt are credited by the user's next message. Scoring is an
            # optimisation, never a reason to fail a turn that already succeeded, so a disk error here is swallowed.
            try:
                memory_policy.record(self.policy, list(self.policy_retrieved))
                if self.policy["turn"] % memory_policy.FORGET_EVERY == 0:
                    self.forget_memories()
                memory_policy.save(self.policy_file, self.policy)
            except Exception as err:
                self.log(f"[yellow]Memory retention bookkeeping failed: {err}[/yellow]")
        result.memory_due = self.memory_enabled and len(self.full_history) > 0 and len(self.full_history) % self.memory_every() == 0
        return result

    def forget_memories(self):
        # The forgetting pass: a memory that has proved no use is dropped, and one that never came up is dropped once
        # it is old enough. Only the character's own automatic entries and its episodes are candidates. An entry the
        # user wrote is never touched, and the character card and memory bank are never changed.
        self.policy_forgotten = []
        pdir = self.palace_dir or self.ai_dir
        entries, cfg = palace.load_all(pdir)
        core = palace_audit.core_keys(pdir)   # core memory is never forgotten, even when the character wrote it
        kept = []
        for entry in entries:
            key = palace.palace_key(entry)
            if entry.get("auto") and key not in core and memory_policy.should_forget(self.policy, key):
                self.policy_forgotten.append(entry["title"] or entry["content"][:60])
                continue
            kept.append(entry)
        if len(kept) != len(entries):
            palace.save(self.palace_dir or self.ai_dir, kept,
                        {"recursive": cfg["recursive"], "budget": cfg["budget"], "auto": cfg["auto"],
                         "episodes": cfg["episodes"]})

        live = [ep for ep in self.episodes
                if not memory_policy.should_forget(self.policy, episodes.key_for(ep))]
        if len(live) != len(self.episodes):
            self.policy_forgotten += [str(ep.get("text") or "")[:60] for ep in self.episodes if ep not in live]
            self.episodes = live
            self._save_episodes()

        # Rows for memories that are no longer stored would otherwise pile up for every episode ever cut.
        keys = [palace.palace_key(e) for e in kept] + [episodes.key_for(ep) for ep in self.episodes]
        memory_policy.prune(self.policy, keys)
        return list(self.policy_forgotten)

    # What the settings page shows: the scored memories that still exist, strongest first, plus the outcome counts.
    def memory_policy_report(self):
        entries, _cfg = palace.load_all(self.palace_dir or self.ai_dir)
        live = {palace.palace_key(e): ("Mind Palace", e["title"] or e["content"][:60])
                for e in entries if e.get("auto")}
        for ep in self.episodes:
            live[episodes.key_for(ep)] = ("Episode", str(ep.get("text") or "")[:60])
        items = memory_policy.report(self.policy, live)
        return {"enabled": bool(self.policy_enabled), "turn": int(self.policy.get("turn", 0) or 0),
                "items": items, "kept": sum(1 for i in items if i["retention"] >= memory_policy.FLOOR),
                "at_risk": sum(1 for i in items if i["retention"] < memory_policy.FLOOR),
                "forgotten": list(self.policy_forgotten)}

    def sandbox_active(self):
        return self.sandbox_root is not None and not self.is_group

    def reasoning_for(self, model):
        # The level to send for one model. A level the tier no longer allows falls back to its first, and
        # DeepSeek stays on low unless every level is allowed (a pro account or an admin).
        level = self.reasoning if self.reasoning in self.reasoning_levels else (
            self.reasoning_levels[0] if self.reasoning_levels else "low")
        if str(model or "").startswith("deepseek:") and set(self.reasoning_levels) != set(REASONING_LEVELS):
            level = "low"
        return level

    def create_chat_session(self, model_name):
        contents = self._window(self.window_size(), labelled=False)
        active = self.sandbox_active()
        emotions = self.emotions_active()
        return self.client.chats.create(
            model=model_name,
            history=contents if contents else None,
            config=types.GenerateContentConfig(
                system_instruction=(self.system_prompt + (SANDBOX_NOTE if active else "")
                                    + (EMOTION_NOTE if emotions else "")),
                temperature=self.temperature_value(),
                safety_settings=safety_settings(),
                thinking_config=thinking_config_for(model_name, self.thinking, self.reasoning_for(model_name)),
                tools=_tools(active),
            ),
        )

    def emotions_active(self):
        # Emotion images are a solo character's own switch. A group checks each member's folder as it speaks.
        if self.is_group or self.is_novel:
            return False
        try:
            return expressions.state(self.ai_dir)["emotions"]
        except OSError:
            return False

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
        parts = self._window_parts(size, bool(self.is_group), total + 1)
        texts = [text for _, text in parts["formatted"]]
        window_tokens = sum(self.estimate_tokens(text) for text in texts)
        system_text = self._window_system()
        if self.is_group:
            # each member answers with its own block after the shared system text; the largest block is the estimate
            block_tokens = max((self.estimate_tokens(self._member_block(m)[0]) for m in self.group_members), default=0)
            system_tokens = self.estimate_tokens(system_text) + block_tokens
        else:
            system_tokens = self.estimate_tokens(system_text) + self.estimate_tokens(self._turn_notes())
        # The provider reuses the prompt from its first character up to the first change. The system text and the
        # window texts sent last time are the same as now up to that point, so that part is counted as reused.
        reused = 0
        if self._sent_system == system_text:
            shared = 0
            for old, new in zip(self._sent_texts, texts):
                if old != new:
                    break
                shared += self.estimate_tokens(new)
            reused = self.estimate_tokens(system_text) + shared
        return {"window_start": parts["start"], "total": total, "window_size": size, "is_group": bool(self.is_group),
                "system_tokens": system_tokens, "window_tokens": window_tokens, "cached_tokens": min(reused, system_tokens + window_tokens),
                "quench": bool(self.quench_enabled), "quench_saved": (parts["report"] or {}).get("saved_tokens", 0),
                "clip": bool(self.clip_enabled), "clip_saved": parts["clip_saved"],
                "cached_weight": self.budget_weight()}

    # What one cached prompt token counts for in the token budget. A tenth on DeepSeek, a whole token otherwise. The
    # model is the one the next prompt goes to: the pinned one first, as _send_solo and the director pick it.
    def budget_weight(self):
        models = self.group_models if self.is_group else self.fallback_models
        model = (self.ordered_models(models) or [self.model_name])[0]
        return llm_backends.CACHED_TOKEN_BUDGET_WEIGHT if str(model or "").startswith("deepseek:") else 1.0

    # Where the window starts, for a chat of total stored messages. It moves in steps of WINDOW_STEP, not one message a
    # turn, so the history prefix is the same for several turns running. Messages before it are the ones the summary
    # and episodes cover.
    def window_start(self, total, size=None):
        size = self.window_size() if size is None else size
        # The window starts a whole number of steps before the clip boundary, so both move on the same turn and the
        # older part of the window (from its start up to the boundary) is the same for every turn between two steps.
        step = self.window_step()
        back = max(1, round((size - CLIP_KEEP) / step))
        return max(0, self.clip_cut(total) - back * step)

    # The step the window and the clip boundary move in. A group on DeepSeek uses its memory interval, so its history
    # stays the same between saves, as a solo DeepSeek chat's does. A solo DeepSeek chat is anchored at its save instead
    # (memory_anchor), and every other chat keeps WINDOW_STEP.
    def window_step(self):
        if self.cache_anchored() or not self._first_model_deepseek(self.is_group):
            return WINDOW_STEP
        return self.memory_every()

    # The clip boundary for a chat of total messages: messages before it are cut, the rest are sent whole. It moves
    # in steps of window_step() and keeps at least CLIP_KEEP messages whole.
    def clip_cut(self, total):
        step = self.window_step()
        return max(0, ((total - CLIP_KEEP) // step) * step)

    # Where the summary and the episodes stop for a chat of total messages: messages before this point are the ones
    # they cover. With clipping on, that is the clip boundary, so a message is summarised as soon as it is clipped and
    # the window's cut messages are never left out of the summary. With clipping off it is the window start.
    def summary_cut(self, total):
        return self.clip_cut(total) if self.clip_enabled else self.window_start(total)

    # Stage 1 then the gated stage 2, when this account has quenching on. Nothing changes when it is off. The
    # account's own messages are protected from stage 2, so its wording is never cut.
    def _quench(self, texts, roles=None):
        if not self.quench_enabled:
            return list(texts), None
        staged = [quench.strip(self.safe_text(str(t or ""))) for t in texts]
        protect = [r != "model" for r in roles] if roles is not None else None
        return quench.gate(staged, protect=protect)

    # Quenching for a window that starts at chat position start. The older part (up to the clip boundary) gets the
    # gated stage 2, and its result is kept while those messages do not change, so the words sent stay the same
    # between steps and the provider's cache can reuse them. The newest messages get stage 1 only, which is fixed
    # per message. Returns (texts, report).
    def _quench_window(self, raw, roles, start, total):
        if not self.quench_enabled:
            return list(raw), None
        older = max(0, min(len(raw), self.clip_cut(total) - start))
        key = (tuple(raw[:older]), tuple(roles[:older]))
        if self._quench_memo is None or self._quench_memo[0] != key:
            if older:
                staged = [quench.strip(self.safe_text(t)) for t in raw[:older]]
                done, report = quench.gate(staged, protect=[r != "model" for r in roles[:older]])
            else:
                done, report = [], None
            self._quench_memo = (key, list(done), report)
        _, done, report = self._quench_memo
        newer = [quench.strip(self.safe_text(t)) for t in raw[older:]]
        return list(done) + newer, report

    # Cuts messages older than the clip boundary to about CLIP_CHARS, at a word break where one is near. The boundary is
    # fixed to positions in the whole chat and moves only every CLIP_STEP messages, so the same messages stay cut for
    # several turns and the provider's prompt cache can reuse them. At least CLIP_KEEP of the newest messages stay whole.
    # first is the chat position of texts[0] and total is the chat's length. The summary already holds what is cut.
    def _clip_older(self, texts, first, total):
        if not self.clip_enabled:
            return list(texts)
        cut_before = self.clip_cut(total)
        out = []
        for i, text in enumerate(texts):
            if first + i >= cut_before or len(text) <= CLIP_CHARS:
                out.append(text)
                continue
            cut = text[:CLIP_CHARS]
            space = cut.rfind(" ")
            if space > CLIP_CHARS * 0.6:
                cut = cut[:space]
            out.append(cut.rstrip() + " ...")
        return out

    # The window as the model will see it, before it is turned into content: its start, each message as (role, text),
    # the quench report and how many tokens clipping saved. Used by the send and by the next-prompt estimate alike.
    def _window_parts(self, size, labelled, total):
        if self.cache_anchored():
            return self._anchored_parts(labelled, total)
        start = self.window_start(total, size)
        recent = self.full_history[start:total]
        # A chat can begin on a reply once the user has deleted the message it answered. The first content sent to
        # the model should be the user's, so leading replies are left out of the prompt.
        while recent and recent[0].get("role") != "user":
            recent = recent[1:]
            start += 1
        roles = [e.get("role", "user") for e in recent]
        raw = [str(e.get("text") or "") for e in recent]
        quenched, report = self._quench_window(raw, roles, start, total)
        clipped = self._clip_older(quenched, start, total)
        clip_saved = (sum(len(a) - len(b) for a, b in zip(quenched, clipped)) + 3) // 4
        formatted = []
        for e, role, text in zip(recent, roles, clipped):
            if labelled:
                text = f"[{e.get('speaker', role)}]: {text}"
            formatted.append((role, text))
        return {"start": start, "formatted": formatted, "report": report, "clip_saved": clip_saved}

    def _window(self, size, labelled):
        parts = self._window_parts(size, labelled, len(self.full_history))
        self.last_quench = parts["report"]
        self._sent_texts = [text for _, text in parts["formatted"]]
        self._sent_system = self._window_system()
        return [types.Content(role=role, parts=[types.Part.from_text(text=text)]) for role, text in parts["formatted"]]

    # The system text the window's prompt is sent with: the chat's own prompt for a solo chat, and the text every
    # group member shares for a group chat (each member's own block comes after the history).
    def _window_system(self):
        return self._group_system() if self.is_group else self.system_prompt

    def _call(self, chat, text, speaker, on_delta, on_think=None):
        # Send one message. With on_delta, stream the reply and call on_delta(speaker, text_so_far)
        # as each chunk arrives. With on_think, reasoning streams the same way. Returns an object with
        # .text, .thought and .usage_metadata, like a normal response. A sandbox call with tools runs
        # without streaming, so its file round trips finish before the reply is shown.
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
        # Run sandbox file-tool round trips until the model answers in text. Replies are not streamed
        # here: the whole answer is passed to on_delta once it is finished. The first model call is
        # counted by _send_solo; every later call is counted here, so each Gemini call counts once.
        total = {"prompt_token_count": 0, "candidates_token_count": 0, "total_token_count": 0}
        response = None
        for rnd in range(MAX_TOOL_ROUNDS + 1):
            if rnd > 0:
                self._count_request()
            response = chat.send_message(message)
            usage = getattr(response, "usage_metadata", None)
            for key in total:
                value = _usage(usage, key)
                if isinstance(value, int):
                    total[key] += value
            calls = _function_calls(response)
            if not calls:
                break
            parts = []
            for call in calls:
                args = dict(call.args or {})
                parts.append(types.Part.from_function_response(
                    name=call.name, response=sandbox.run_tool(self.sandbox_root, call.name, args)))
            message = parts
        answer = _text(response) if response is not None else None
        if on_delta and answer:
            on_delta(speaker, answer)
        return SimpleNamespace(text=answer or None, thought="", usage_metadata=SimpleNamespace(**total))

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
                response = _run_with_limit(self._call, progress, chat, self._turn_message(processed), self.character_name,
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
        emotion = ""
        if self.emotions_active():
            raw, emotion = take_emotion_tag(raw)
        self.full_history.append({"role": "user", "text": processed, "ts": _now_iso()})
        self.full_history.append({"role": "model", "text": raw, "ts": _now_iso(),
                                  **({"thinking": thought} if thought else {}),
                                  **({"emotion": emotion} if emotion else {})})

        result.model = self.model_name
        result.replies = [Reply(self.character_name, self.character_color, raw, self.restore(raw),
                                self.model_name, get_request_bar(), thought, emotion)]
        return result

    # A group turn: the director picks the first speakers, then members may answer each other, up to AUTO_REPLY_LIMIT.
    # No member may give more than GROUP_CONSECUTIVE replies in a row within the turn.
    def _send_group(self, processed, on_delta=None, on_think=None):
        result = TurnResult(ok=True)
        selected, note = self._select_members(processed)
        result.selected = selected
        result.selector_note = note

        self.full_history.append({"role": "user", "speaker": "user", "text": processed, "ts": _now_iso()})
        usage = {"total": 0, "prompt": 0, "cached": 0, "output": 0}
        streak = {"name": None, "count": 0}   # who spoke last in this turn, and how many times in a row
        for name in selected:
            if self._blocked(name, streak):
                continue
            # the user's line is already the newest entry of the history each member reads, so it is not sent again
            if self._member_reply(name, processed, result, usage, on_delta, on_think, in_history=True):
                self._record(streak, name)

        # Members answer each other: the director names one more speaker after each reply, until it says NONE.
        auto = []
        for _ in range(AUTO_REPLY_LIMIT if result.replies else 0):
            candidates = [m for m in self.group_members if not self._blocked(m, streak)]
            if not candidates or not streak["name"]:
                break
            nxt, failure = self._select_next(processed, candidates, streak["name"])
            if nxt is None:
                if failure:
                    result.selector_note = (result.selector_note or "") + f" Auto replies stopped: {failure}."
                break
            if not self._member_reply(nxt, AUTO_REQUEST, result, usage, on_delta, on_think):
                break
            self._record(streak, nxt)
            auto.append(display_for(nxt))
        if auto:
            result.selector_note = (result.selector_note or "") + f" Members answered each other: {auto}."

        if not result.replies:
            # nobody answered: drop the user line so history never holds an unanswered turn
            self.full_history.pop()
            result.ok = False
            result.error = result.error or "No group member replied."
            return result

        result.turn_tokens = usage["total"]
        result.total_tokens = usage["total"]
        result.prompt_tokens = usage["prompt"]
        result.cached_tokens = usage["cached"]
        result.response_tokens = usage["output"]
        result.model = self.model_name
        return result

    # The per-member cap: a member who has already given GROUP_CONSECUTIVE replies in a row may not speak again yet.
    @staticmethod
    def _blocked(name, streak):
        return streak["name"] == name and streak["count"] >= GROUP_CONSECUTIVE

    @staticmethod
    def _record(streak, name):
        streak["count"] = streak["count"] + 1 if streak["name"] == name else 1
        streak["name"] = name

    def _member_reply(self, name, request, result, usage, on_delta=None, on_think=None, in_history=False):
        # One member's reply, added to the turn and to the chat history. Tries each group model in turn.
        # Returns False when no model gave a reply.
        label = display_for(name)
        self._count_request()
        block, color = self._member_block(name)
        contents = self._window(self.window_size(), labelled=True)
        try:
            emotion_on = expressions.state(name)["emotions"]
        except OSError:
            emotion_on = False
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
                    config=types.GenerateContentConfig(
                        system_instruction=self._group_system() + (EMOTION_NOTE if emotion_on else ""),
                        temperature=self.temperature_value(),
                        thinking_config=thinking_config_for(model, self.thinking, self.reasoning_for(model)),
                        tools=_tools(False)),
                )
                ask = (f"[{label} respond to the group, answering the user's last message above]" if in_history
                       else f"[{label} respond to the group]: {request}")
                message = block + "\n\n" + self._turn_notes() + ask
                resp = _run_with_limit(self._call, progress, chat, message, label,
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
            return False
        raw = text.strip()
        thought = getattr(resp, "thought", "") or ""
        emotion = ""
        if emotion_on:
            raw, emotion = take_emotion_tag(raw)
        result.replies.append(Reply(label, color, raw, self.restore(raw), self.model_name, get_request_bar(),
                                    thought, emotion))
        self.full_history.append({"role": "model", "speaker": name, "text": raw, "ts": _now_iso(),
                                  **({"thinking": thought} if thought else {}),
                                  **({"emotion": emotion} if emotion else {})})
        found = getattr(resp, "usage_metadata", None)
        for key, value in (("prompt", _usage(found, "prompt_token_count")),
                             ("cached", _usage(found, "cached_content_token_count")),
                             ("output", _usage(found, "candidates_token_count"))):
            if isinstance(value, int):
                usage[key] += value
        if found and hasattr(found, "total_token_count") and found.total_token_count:
            usage["total"] += found.total_token_count
        return True

    # The text every group member shares. It is the system instruction of every member's call, so the members' calls
    # begin with the same text and the provider can reuse the history that follows it. Nothing here is specific to one
    # member, and it changes only when the chat's world or its reply length changes.
    def _group_system(self):
        text = (f"You are one member of the group chat '{self.character_name}'. Each message names the member you are "
                "playing and gives that member's character details. Speak only as that member.")
        if self.world_text:
            text += "\n\n--- The world this story is set in ---\n" + self.world_text
        return (text + NARRATIVE_INSTRUCTION + DIALOGUE_INSTRUCTION + FORMAT_INSTRUCTION + self.reply_length_clause()
                + NOTES_GUIDE)

    # One member's own text: its card, its note from the user and its examples, sent in the message just before the
    # reply is asked for, after the shared history. Returns (text, colour).
    def _member_block(self, name):
        char_file = os.path.join(name, "character.md")
        block = (f"You are {display_for(name)}, participating in a group chat. CRITICAL: Provide a unique response to the user's input. "
                 "Do not repeat what other group members have already said in this turn. Focus on your specific character's perspective.")
        color = "cyan"
        if os.path.exists(char_file):
            content = _read_text(char_file)
            block = content + GROUP_RULE
            for line in content.splitlines():
                if "colour:" in line.lower() or "color:" in line.lower():
                    parts = line.split(":")
                    if len(parts) > 1:
                        col = parts[1].strip().lower()
                        if col:
                            color = get_safe_color(col, "cyan")
        note = self.member_notes.get(name)
        if note:
            block += "\n\n--- Note from the user for you in this group ---\n" + note
        # A group member's own example messages sit beside its card, exactly as in a solo chat.
        examples = examples_mod.prompt_block(examples_mod.load(name), display_for(name))
        if examples:
            block += "\n\n" + examples
        return f"--- Your character: {display_for(name)} ---\n" + block, color

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

    # The first line of a character card that is not a heading, with a length cap. The director reads this line, not the
    # whole card; the member's own reply still reads the full card.
    def _persona_line(self, path, limit=200):
        try:
            text = _read_text(path)
        except Exception:
            return ""
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                return line[:limit]
        return ""

    # The chat position the director's history starts at when the director runs on DeepSeek. A memory save runs every
    # MEMORY_EVERY messages and cuts at summary_cut of the total at that point, so this is the cut of the last save point.
    # It moves only at those points, which is what keeps the history the same between them. Without memory saves it still
    # moves on the same schedule, so the history never grows past one save period.
    def memory_anchor(self, total):
        period = self.memory_every()
        saved_at = (total // period) * period
        base = self.summary_cut(saved_at) if saved_at > 0 else 0
        # After an idle save the history starts at its last few messages, until the next period's save moves past them.
        if self.idle_anchor is not None and base < self.idle_anchor <= total:
            return self.idle_anchor
        return base

    # Messages between memory saves. A solo chat on DeepSeek saves every 30 turns, so its history can stay cached that long.
    def memory_every(self):
        # A solo chat on DeepSeek saves every DEEPSEEK_SOLO_MEMORY_EVERY messages, a group on DeepSeek every
        # DEEPSEEK_MEMORY_EVERY. Other chats keep MEMORY_EVERY.
        if self.cache_anchored():
            return DEEPSEEK_SOLO_MEMORY_EVERY
        return DEEPSEEK_MEMORY_EVERY if self._first_model_deepseek(self.is_group) else MEMORY_EVERY

    # True for a solo chat whose first model is DeepSeek. Its prompt history starts at the last memory save and is kept
    # the same until the next one, so the provider's cache can reuse it. Group members keep the sliding window.
    def cache_anchored(self):
        if self.is_group:
            return False
        return self._first_model_deepseek(False)

    # True when the first model a chat would try is a DeepSeek one. group picks the group's models, else the chat's own.
    def _first_model_deepseek(self, group):
        models = self.group_models if group else self.fallback_models
        model = (self.ordered_models(models) or [self.model_name])[0]
        return str(model or "").startswith("deepseek:")

    # The solo prompt history on DeepSeek: from the memory anchor to the newest message, stage-one quenched and not clipped.
    # The anchor moves only at a save, so the history is the same text between saves.
    def _anchored_parts(self, labelled, total):
        start = self.memory_anchor(total)
        recent = self.full_history[start:total]
        # The first content sent should be the user's, as in the sliding window.
        while recent and recent[0].get("role") != "user":
            recent = recent[1:]
            start += 1
        roles = [e.get("role", "user") for e in recent]
        texts = self._stage_one_texts(recent)
        formatted = []
        for e, role, text in zip(recent, roles, texts):
            if labelled:
                text = f"[{e.get('speaker', role)}]: {text}"
            formatted.append((role, text))
        return {"start": start, "formatted": formatted, "report": None, "clip_saved": 0}

    # Stage 1 quenching only, one message at a time, so a message reads the same whichever history it sits in. The gated
    # stage 2 looks at the whole list, so the anchored history leaves it out.
    def _stage_one_texts(self, recent):
        raw = [str(e.get("text") or "") for e in recent]
        if not self.quench_enabled:
            return raw
        return [quench.strip(self.safe_text(text)) for text in raw]

    def _director_context(self, processed, window, director_model):
        # What the director reads before it picks: members, chat so far, Mind Palace matches. Shared by both picks.
        labels = {display_for(m): m for m in self.group_members}   # display name -> folder name
        shown = list(labels)

        bios = []
        for name in self.group_members:
            snippet = f"Member: {display_for(name)}"
            char_file = os.path.join(name, "character.md")
            if os.path.exists(char_file):
                line = self._persona_line(char_file)
                if line:
                    snippet += f"\nPersona: {line}"
            if self.member_notes.get(name):
                snippet += f"\nNote from the user about this member:\n{self.member_notes[name]}"
            bios.append(snippet)

        group_memory = ""
        if os.path.exists(self.memory_bank_file):
            try:
                group_memory = _read_text(self.memory_bank_file)[:2000]
            except Exception:
                group_memory = ""
        total = len(self.full_history)
        if str(director_model or "").startswith("deepseek:"):
            # DeepSeek: the history runs from the memory anchor to the newest message. It is the same text between memory
            # saves, so the provider's cache covers all of it, and the director's two picks share it.
            recent = self.full_history[self.memory_anchor(total):]
            texts = self._stage_one_texts(recent)
        else:
            recent = self.full_history[-window:]
            texts, _report = self._quench([str(e.get("text") or "") for e in recent],
                                          [e.get("role", "user") for e in recent])
            texts = self._clip_older(texts, max(0, total - window), total)
        history_text = "\n".join(f"[{display_for(e['speaker']) if 'speaker' in e else e['role']}]: {t}"
                                 for e, t in zip(recent, texts))
        parts = [
            f"You are the director of the group chat '{self.character_name}'.\n",
            f"Available members: {shown}\n\n",
            "Member details, notes and memories:\n", "\n---\n".join(bios), "\n\n",
        ]
        if self.summary_text:
            parts.append(f"Chat summary so far:\n{self.summary_text}\n\n")
        if group_memory:
            parts.append(f"Group memory:\n{group_memory}\n\n")
        # Recall changes every turn, so it sits after the history: the stable part before it (members, notes, summary,
        # group memory) and the history itself can then be reused from the provider's cache.
        parts.append(f"Recent chat history:\n{history_text}\n\n")
        recall = self._recall_text()
        if recall:
            parts.append(recall.strip() + "\n\n")
        parts.append(f"Current user prompt / input: {processed}\n\n")
        return "".join(parts)

    # The model the director runs on: the pinned pick, else the first chain model.
    def _director_model(self):
        return (self.ordered_models(self.group_models) or [self.model_name])[0]

    def _select_members(self, processed):
        # The director reads the whole context and returns who speaks, in what order. It uses the same model
        # list as the members, so a DeepSeek or Claude pick runs the director on that provider too.
        members = self.group_members
        if not members:
            return [], None
        # With one member the pick is forced, so the director is not asked.
        if len(members) == 1:
            return list(members), f"Group chat active: the only member speaks: {[display_for(members[0])]} (OwO)"
        labels = {display_for(m): m for m in members}   # display name -> folder name
        shown = list(labels)
        director_model = self._director_model()
        prompt = self._director_context(processed, SELECTOR_WINDOW, director_model) + (
            f"CRITICAL INSTRUCTION: Decide which members respond to this input and the order they speak in, using the "
            f"user's prompt and all the context above. Choose between 1 and {len(members)} members, only from {shown}. "
            "Leave out any member who should not speak this turn.\n"
            "Return ONLY the chosen names in speaking order, separated by commas (e.g. CharacterB, CharacterA), "
            "with no extra text or explanation."
        )

        # A failed call is not retried on another model; the turn falls back to one random member and says why.
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

    # After a reply, the director names the one member who answers next, from the candidates, or NONE.
    # Returns (folder name or None, failure text or None).
    def _select_next(self, processed, candidates, last):
        labels = {display_for(m): m for m in candidates}
        director_model = self._director_model()
        prompt = self._director_context(processed, NEXT_WINDOW, director_model) + (
            f"CRITICAL INSTRUCTION: {display_for(last)} has just replied. Decide whether one more member should answer "
            f"right now so the members keep talking to each other. Choose one name from {list(labels)}, or reply NONE "
            "if nobody should speak next.\n"
            "Return ONLY that one name or NONE, with no extra text or explanation."
        )
        try:
            self._count_request(director_model)
            chat = self.client.chats.create(model=director_model, config=types.GenerateContentConfig(temperature=0.7))
            text = _text(chat.send_message(prompt)) or ""
        except Exception as err:
            return None, str(err)[:160]
        found = self._ordered_mentions(text, labels)
        return (found[0] if found else None), None

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
        # Episodes that described the removed messages are dropped, so they never describe other ones.
        self.episodes = episodes.prune(self.episodes, len(self.full_history))
        self.episodes_covered = min(self.episodes_covered, len(self.full_history))
        self._save_episodes()
        return True

    # Remove saved messages by index: one message, a run, or a selection. Returns how many went. The chat is this
    # account's own copy, so nothing shared changes and no model call is made. Positions outside the chat are
    # ignored, and a call that removes nothing returns 0.
    def delete_messages(self, indexes):
        picked = sorted({int(i) for i in indexes if 0 <= int(i) < len(self.full_history)})
        if not picked:
            return 0
        start = self._exchange_start()
        user_line = self.full_history[start] if start is not None else None
        # The saved alternatives belong to the last exchange and mirror it exactly. Once a message of that
        # exchange is gone they would describe a chat that no longer exists, so they go with it.
        touched = start is not None and any(i > start for i in picked)
        for i in reversed(picked):
            del self.full_history[i]
        if touched and user_line is not None and any(e is user_line for e in self.full_history):
            user_line.pop("variants", None)
            user_line.pop("variant", None)
        _write_history(self.memory_file, self.full_history)
        # Episodes and the summary are cut by position, and a removal shifts every position after it. Anything that
        # reaches the first removed point is dropped, and the covered count is pulled back to it, so the messages
        # that moved are read again instead of being described under the wrong index.
        first = picked[0]
        self.episodes = [e for e in self.episodes if e["end"] <= first]
        self.episodes_covered = min(self.episodes_covered, first)
        self._save_episodes()
        self.summary_covered = min(self.summary_covered, first)
        return len(picked)

    def _scene_system(self, base):
        # Instructions plus the card and persona, so suggestions and events fit this chat.
        parts = [base]
        if self.character_prompt:
            parts.append("CHARACTER CARD:\n" + self.character_prompt)
        if self.user_prompt:
            parts.append("USER PERSONA:\n" + self.user_prompt)
        return "\n\n".join(parts)

    def _one_shot(self, system, final_prompt, background=False):
        # One model call that is not saved to the chat. Counted like any other request. Returns (text, model).
        # background=True runs it on the background chain (see _background_models) instead of the chat's own models.
        contents = self._window(self.window_size(), labelled=True)
        if background:
            order = self._background_models()
        else:
            models = self.group_models if (self.is_group and self.group_models) else self.fallback_models
            order = self.ordered_models(models)
        last_err = None
        for model in order:
            try:
                self._count_request(model)
                chat = self.client.chats.create(
                    model=model,
                    history=contents if contents else None,
                    config=types.GenerateContentConfig(system_instruction=system, temperature=0.9,
                                                       safety_settings=safety_settings(),
                                                       thinking_config=thinking_config_for(model, self.thinking, self.reasoning_for(model))),
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

    def checkin(self):
        # A scheduled check-in: one message from the character, saved to the chat and marked scheduled, so the
        # app can show it as missed until the user opens the chat. Written from the time since the user last wrote.
        last = next((e for e in reversed(self.full_history)
                     if e.get("role") == "user" and e.get("speaker") != "narrator" and e.get("ts")), None)
        gap = "a while"
        if last is not None:
            try:
                hours = (datetime.datetime.now(datetime.timezone.utc)
                         - datetime.datetime.fromisoformat(last["ts"])).total_seconds() / 3600
                gap = f"about {round(hours)} hours" if hours >= 2 else f"about {max(1, round(hours * 60))} minutes"
            except (TypeError, ValueError):
                pass
        prompt = (f"The user has not written for {gap}. Write the character's check-in message now, "
                  "as one to three sentences.")
        text, _ = self._one_shot(self._scene_system(CHECKIN_SYSTEM), prompt)
        text = _clean_plain(text)
        if not text:
            raise RuntimeError("the model sent an empty check-in")
        self.full_history.append({"role": "model", "text": text, "ts": _now_iso(), "scheduled": True})
        self.save()
        return text

    def dream(self):
        # Offline reflection: tidies this character's memory bank and automatic palace entries, and links facts.
        # The model calls made here do not go through _count_request or _count_tokens, so they are not counted
        # as the user's requests or tokens. The caller counts them against the server's own dream cap.
        # Returns the record of the dream, or None when there was nothing worth doing.
        self.dream_calls = 0
        if len(self.full_history) - self.dream_len < dreams.MIN_NEW:
            return None
        pdir = self.palace_dir or self.ai_dir
        entries, cfg = palace.load_all(pdir)
        auto = [e for e in entries if e["auto"] and e["id"]]
        memory = _read_text(self.memory_bank_file) if os.path.exists(self.memory_bank_file) else ""
        recent = [e["text"] for e in self.episodes[-dreams.EPISODES_READ:]]
        # Event memory: the dream reads the messages since the last event pass, and links them to the latest events.
        events_on = self.event_memory_enabled
        new_lines = recent_events = None
        new_events, new_evolve = [], []
        if events_on:
            span = self.full_history[self.graph["covered"]:][-eventgraph.NEW_MESSAGES:]
            new_lines = [f"- [{e.get('speaker') or e.get('role')}]: {e['text']}" for e in span if e.get("text")]
            recent_events = eventgraph.recent_for_dream(self.graph)
        prompt = dreams.build_prompt(memory, auto, recent, new_lines, recent_events)
        models = self._memory_models()
        text = None
        for m in models:
            self.dream_calls += 1
            try:
                chat = self.client.chats.create(
                    model=m,
                    config=types.GenerateContentConfig(system_instruction=dreams.SYSTEM, temperature=0.5,
                                                       safety_settings=safety_settings()))
                text = _text(chat.send_message(prompt))
                if text and text.strip():
                    break
            except Exception as err:
                self.log(f"[yellow]Dream with {m} failed: {err}[/yellow]")
                text = None
        if not text:
            return None
        changes, drops, note = dreams.parse(text)
        counts = {"added": 0, "updated": 0, "removed": 0}
        if changes or memory.strip():
            merged, counts = memory_bank.apply(memory, changes)
            with open(self.memory_bank_file, "w", encoding="utf-8") as f:
                f.write(merged)
        dropped = 0
        dropped_entries = []
        if drops:
            dropped_entries = [e for e in entries if e["auto"] and e["id"] in drops]
            keep = [e for e in entries if not (e["auto"] and e["id"] in drops)]
            dropped = len(entries) - len(keep)
            if dropped:
                palace.save(pdir, keep, {"recursive": cfg["recursive"], "budget": cfg["budget"],
                                         "auto": cfg["auto"], "episodes": cfg["episodes"]})
        events_added = evolved = 0
        if events_on:
            new_events, new_evolve = eventgraph.parse_events(text)
            events_added, evolved = eventgraph.apply(self.graph, new_events, new_evolve, _now_iso())
            self.graph["covered"] = len(self.full_history)
            eventgraph.save(self.graph_file, self.graph)
        # Tensions are read in the same quiet hour when this chat keeps them on the dream. A failure here must not
        # lose the dream's own changes, which are already written.
        if self.tension_mode == "dream":
            try:
                self.tension_pass("dream", self._dream_call)
            except Exception as err:
                self.log(f"[yellow]Tension pass after the dream failed: {err}[/yellow]")
        self.dream_len = len(self.full_history)
        items = []
        for line in (changes or "").splitlines():
            line = line.strip()
            kind, sep, payload = line.partition(":")
            kind, payload = kind.strip().upper(), payload.strip()
            if not sep or kind not in ("ADD", "UPDATE", "REMOVE"):
                continue
            if kind == "ADD" and payload.startswith("[Connections]"):
                kind = "LINK"
            items.append({"kind": kind, "text": payload[:dreams.ITEM_MAX]})
        for e in dropped_entries:
            title = (e.get("title") or "").strip()
            content = (e.get("content") or "").strip()
            text = f"{title}: {content}" if title and content else (title or content or "Untitled")
            items.append({"kind": "DROP", "text": text[:dreams.ITEM_MAX]})
        for ev in new_events:
            text = f"{ev.get('actor') or 'Someone'}: {ev.get('event') or ''}".strip()
            if ev.get("consequence"):
                text += f" - {ev['consequence']}"
            items.append({"kind": "EVENT", "text": text[:dreams.ITEM_MAX]})
        for line in new_evolve:
            items.append({"kind": "EVOLVE", "text": str(line)[:dreams.ITEM_MAX]})
        if note:
            items.append({"kind": "NOTE", "text": note[:dreams.ITEM_MAX]})
        items = items[:dreams.MAX_ITEMS]
        record = {"len": self.dream_len, "when": _now_iso(), "note": note, "added": counts["added"],
                  "updated": counts["updated"], "removed": counts["removed"], "dropped": dropped,
                  "events": events_added, "evolved": evolved, "items": items}
        _write_json(self.dream_file, record)
        self._build_system_prompt()
        return record

    def write_story_bible(self, count_requests=True):
        # One wiki page for this chat, rebuilt from every memory source plus the first and latest messages.
        # With count_requests the calls are counted like the reader's own requests; a dream-night call is
        # counted by the caller against the server's dream cap instead. The bible is read-only output: it
        # never touches memory.md, the palace or the history. Returns the saved record, or None.
        self.bible_calls = 0
        history = self.full_history
        if len(history) < story_bible.MIN_HISTORY:
            return None
        memory = _read_text(self.memory_bank_file) if os.path.exists(self.memory_bank_file) else ""
        pinned = _read_text(self.pinned_file) if os.path.exists(self.pinned_file) else ""
        entries, _cfg = palace.load_all(self.palace_dir or self.ai_dir)
        threads_open = [f"{t.get('kind')}: {t.get('text')}" for t in tensions.open_items(self.tensions)]
        events = eventgraph.recent_for_dream(self.graph)
        prompt = story_bible.build_prompt(memory, pinned, entries, self.summary_text, threads_open, events,
                                          story_bible.opening_lines(history), story_bible.recent_lines(history),
                                          self.user_display_name or "the reader")
        models = self._memory_models()
        text = None
        for m in models:
            self.bible_calls += 1
            try:
                if count_requests:
                    self._count_request(m)
                chat = self.client.chats.create(
                    model=m,
                    config=types.GenerateContentConfig(system_instruction=story_bible.SYSTEM, temperature=0.5,
                                                       safety_settings=safety_settings()))
                resp = chat.send_message(prompt)
                if count_requests:
                    usage = getattr(resp, "usage_metadata", None)
                    total = usage.total_token_count if usage and hasattr(usage, "total_token_count") else 0
                    if isinstance(total, int) and total > 0:
                        self._count_tokens(total, m)
                text = _text(resp)
                if text and text.strip():
                    break
            except Exception as err:
                self.log(f"[yellow]Story bible with {m} failed: {err}[/yellow]")
                text = None
        if not text:
            return None
        record = story_bible.parse(text)
        if record is None:
            self.log("[yellow]The story bible reply was not valid JSON; the last one is kept.[/yellow]")
            return None
        story_bible.with_stamp(record, _now_iso(), len(history))
        story_bible.save(self.bible_file, record)
        return record

    def import_doc(self, text):
        # Reads an uploaded reference document (backstory, novel excerpt, setting PDF) once and mines its
        # lasting lore for review. Counted like the reader's own requests. Nothing is saved here. Returns
        # {"palace": [{title, keys, content}, ...], "facts": [{section, text}, ...]}, or raises when no
        # model came back with a readable mining.
        self.import_calls = 0
        text = str(text or "").strip()[:docextract.TEXT_LIMIT]
        if not text:
            raise RuntimeError("the document holds no readable text")
        models = self._memory_models()
        last_err = None
        for m in models:
            self.import_calls += 1
            try:
                self._count_request(m)
                chat = self.client.chats.create(
                    model=m,
                    config=types.GenerateContentConfig(system_instruction=docextract.SYSTEM, temperature=0.4,
                                                       safety_settings=safety_settings()))
                resp = chat.send_message(text)
                usage = getattr(resp, "usage_metadata", None)
                total = usage.total_token_count if usage and hasattr(usage, "total_token_count") else 0
                if isinstance(total, int) and total > 0:
                    self._count_tokens(total, m)
                result = docextract.parse(_text(resp) or "")
                if result is not None:
                    return result
                last_err = RuntimeError("the reply was not valid JSON")
            except Exception as err:
                last_err = err
        raise RuntimeError(f"no model could read the document ({last_err})")

    # One off-screen pass: the model writes what the character did between sessions, and sometimes a twist.
    # The calls here do not go through _count_request or _count_tokens, so they are not counted as the user's
    # requests or tokens. The caller counts them against the server's own director cap. Returns (fragments, twists).
    def director(self, record):
        self.director_calls = 0
        recent = [f"- [{e.get('speaker') or e.get('role') or 'user'}]: {str(e['text'])[:300]}"
                  for e in self.full_history[-offscreen.NEW_MESSAGES:] if e.get("text")]
        threads = tensions.prompt_block(self.tensions) if self.tension_mode != "off" else ""
        prompt = offscreen.build_prompt(self.character_prompt, recent, record, threads)
        models = self._memory_models()
        text = None
        for m in models:
            self.director_calls += 1
            try:
                chat = self.client.chats.create(
                    model=m,
                    config=types.GenerateContentConfig(system_instruction=offscreen.SYSTEM, temperature=0.8,
                                                       safety_settings=safety_settings()))
                text = _text(chat.send_message(prompt))
                if text and text.strip():
                    break
            except Exception as err:
                self.log(f"[yellow]Off-screen pass with {m} failed: {err}[/yellow]")
                text = None
        if not text:
            return [], []
        return offscreen.parse(text)

    # One tension pass over the messages since the last one: it opens the tensions the chat sets up and closes the ones
    # it resolves. It runs only when the chat's mode is when ("dream" from the nightly dream, "save" from a memory
    # bank save). before_call(model) is called before each model attempt so the caller can count it. Returns the
    # number of attempts made. The read position moves only when a reply came back, so a failed pass is retried.
    def tension_pass(self, when, before_call):
        if self.tension_mode != when:
            return 0
        lines = tensions.new_lines(self.tensions, self.full_history)
        if not lines:
            return 0
        prompt = tensions.build_prompt(self.tensions, lines)
        models = self._memory_models()
        text, attempts = None, 0
        for m in models:
            attempts += 1
            before_call(m)
            try:
                chat = self.client.chats.create(
                    model=m,
                    config=types.GenerateContentConfig(system_instruction=tensions.SYSTEM, temperature=0.5,
                                                       safety_settings=safety_settings()))
                text = _text(chat.send_message(prompt))
                if text and text.strip():
                    break
            except Exception as err:
                self.log(f"[yellow]Tension pass with {m} failed: {err}[/yellow]")
                text = None
        if not text:
            return attempts
        opened, resolved = tensions.parse(text)
        tensions.apply(self.tensions, opened, resolved, turn=len(self.full_history))
        self.tensions["covered"] = len(self.full_history)
        tensions.save(self.tensions_file, self.tensions)
        return attempts

    # A tension closed by hand from the page. The reader decides the resolution text.
    def resolve_tension(self, tid, how):
        tensions.apply(self.tensions, [], [{"id": tid, "how": how or "Resolved by hand."}], turn=len(self.full_history))
        tensions.save(self.tensions_file, self.tensions)

    # A tension removed from the record entirely, open or resolved.
    def drop_tension(self, tid):
        self.tensions["items"] = [i for i in self.tensions["items"] if i["id"] != tid]
        tensions.save(self.tensions_file, self.tensions)

    def _dream_call(self, model):
        self.dream_calls += 1

    def ask(self, system, prompt, background=False):
        # One call with the scene in view, not saved to the chat. Returns the text the model wrote.
        text, _ = self._one_shot(self._scene_system(system), prompt, background=background)
        return text

    def roll_event(self):
        # Add one random event to the chat as a narrator note. It is saved, so the next reply sees it.
        text, _ = self._one_shot(self._scene_system(ROLL_SYSTEM), "Write the random event now.")
        text = _clean_plain(text)
        if not text:
            raise RuntimeError("the model sent an empty event")
        self.full_history.append({"role": "user", "speaker": "narrator", "text": "Random event: " + text, "ts": _now_iso()})
        self.save()
        return text

    def append_narrator(self, text, game=False):
        # A note the app wrote itself: a mini-game result, or a line the character said during one.
        # Saved like a roll event, so swipe and regenerate treat it as narration and leave it in place.
        entry = {"role": "user", "speaker": "narrator", "text": text, "ts": _now_iso()}
        if game:
            entry["game"] = True
        self.full_history.append(entry)
        self.save()
        return entry["ts"]

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

    def _load_episodes(self):
        data = _read_json(self.episodes_file, {})
        if isinstance(data, dict) and isinstance(data.get("episodes"), list):
            self.episodes = [e for e in data["episodes"] if isinstance(e, dict) and {"start", "end", "keys", "text"} <= e.keys()]
            self.episodes_covered = max(0, int(data.get("covered", 0) or 0))

    def _save_episodes(self):
        _write_json(self.episodes_file, {"covered": self.episodes_covered, "episodes": self.episodes})
    def _add_episodes(self, cutoff):
        # Messages before cutoff have left the recent window, so they are cut into episodes once.
        if cutoff <= self.episodes_covered:
            return
        run = self.full_history[self.episodes_covered:cutoff]
        _, pcfg = palace.load_all(self.palace_dir or self.ai_dir)
        if self.episodes_enabled and pcfg["episodes"]:
            speakers = [self.character_name, self.user_display_name] + ([display_for(m) for m in self.group_members] if self.is_group else [])
            new = episodes.make(run, self.episodes_covered, speakers)
            # One batch call for all the new episodes. If it fails they are kept, and found by keyword only.
            if new:
                vectors = episodes.embed(self.client, [e["text"] for e in new])
                if vectors is not None:
                    self._count_request(episodes.EMBED_MODEL)
                    self._count_tokens(episodes.estimate_tokens([e["text"] for e in new]), episodes.EMBED_MODEL)
                    for e, v in zip(new, vectors):
                        e["vec"] = v
            self.episodes = episodes.trim(self.episodes + new)
        # With episodic memory off, these messages still count as covered, so turning it on later does not backfill.
        self.episodes_covered = cutoff
        self._save_episodes()

    def _load_summary(self):
        data = _read_json(self.summary_file, {})
        # The idle save's state: where the next message's history starts, and how much of the chat it covers.
        self.idle_anchor = None
        self.idle_done = 0
        # The first model the chat used on its last message. A different one on the next message means the user switched
        # models, and the chat is saved before that message goes out.
        self.last_first_model = None
        if isinstance(data, dict):
            self.last_first_model = str(data.get("last_model") or "") or None
            try:
                self.idle_anchor = int(data["idle_anchor"]) if data.get("idle_anchor") is not None else None
                self.idle_done = max(0, int(data.get("idle_done", 0)))
            except (TypeError, ValueError):
                self.idle_anchor, self.idle_done = None, 0
        if isinstance(data, dict):
            self.summary_text = str(data.get("text") or "").strip()
            try:
                self.summary_covered = max(0, int(data.get("covered", 0)))
            except (TypeError, ValueError):
                self.summary_covered = 0

    def _save_summary(self, text, covered):
        self.summary_text = text
        self.summary_covered = covered
        self._write_summary_state()

    def _write_summary_state(self):
        _write_json(self.summary_file, {"text": self.summary_text, "covered": self.summary_covered,
                                        "idle_anchor": self.idle_anchor, "idle_done": self.idle_done,
                                        "last_model": self.last_first_model})

    def _chat_first_model(self):
        return str((self.ordered_models(self.fallback_models) or [self.model_name])[0] or "")

    # A solo chat whose model changed since its last message gets a save that covers all but the last IDLE_KEEP_MESSAGES
    # messages, before the next message goes out. On DeepSeek the prompt then starts from that save and those messages, so
    # the new provider's cold cache is not asked to take the whole history. The save runs where the history is already
    # cached, or as plain text on the Gemini chain when it is not: either way the new model's first message does not pay
    # the whole history at the cache-miss rate.
    def model_switch_save(self):
        if self.is_group:
            return False
        now = self._chat_first_model()
        prev = self.last_first_model
        if prev == now:
            return False
        if prev is None and not str(now).startswith("deepseek:"):
            # No recorded model to compare with: only a move to DeepSeek is worth a save, because its prompt is the one
            # that starts from a cold cache. A chat with no history is left alone by the length check below.
            self.last_first_model = now
            self._write_summary_state()
            return False
        saved = False
        if self.memory_enabled and len(self.full_history) > max(self.idle_done, IDLE_KEEP_MESSAGES):
            before = self.idle_done
            try:
                if prev and str(prev).startswith("deepseek:") and self.cache_anchored():
                    # DeepSeek to DeepSeek: the anchored history is cached under the model that last answered, so the
                    # save runs there and reuses it. The Gemini chain follows in case that model fails.
                    self.update_memory_bank(idle=True, models=[prev] + self._background_models())
                else:
                    self.update_memory_bank(idle=True, plain=True)
                saved = self.idle_done != before
            except Exception as err:
                self.log(f"[yellow]Save before the model switch failed: {err}[/yellow]")
        self.last_first_model = now
        self._write_summary_state()
        return saved

    # A DeepSeek solo chat that has been idle gets a save covering all of it but the last IDLE_KEEP_MESSAGES. The next
    # message then starts from those, not from the older history, which the cache may no longer hold. Returns True when
    # the save made the change. The state file is written even when nothing changed, so an idle chat is not retried
    # until a new message arrives.
    # The length the chat's last request saw: the history before the reply just written, when there is one. A save made
    # after a reply starts from the anchor that request used, not the anchor the save itself moves to.
    def save_base(self, total):
        return total - 1 if total and self.full_history[-1].get("role") != "user" else total

    def idle_save(self):
        if not self.cache_anchored() or not self.memory_enabled or len(self.full_history) <= self.idle_done:
            self._write_summary_state()
            return False
        before = self.idle_done
        self.update_memory_bank(idle=True)
        self._write_summary_state()
        return self.idle_done != before

    # The save for a DeepSeek solo chat: the chat's own system prompt and history come first, so the save reuses the
    # cached prefix, and the save instructions are the last message.
    def _cached_save_call(self, model, missing_text, want_summary):
        active = self.sandbox_active()
        system = self.system_prompt + (SANDBOX_NOTE if active else "") + (EMOTION_NOTE if self.emotions_active() else "")
        # The save starts from the anchor the chat's last request used, so its prompt shares that cached prefix. The reply
        # just written, and anything after the chat's request, follow the prefix as text.
        total = len(self.full_history)
        chat_total = self.save_base(total)
        parts = self._window_parts(self.window_size(), False, chat_total)
        formatted = list(parts["formatted"])
        later = self.full_history[chat_total:total]
        formatted += [(e.get("role", "user"), text) for e, text in zip(later, self._stage_one_texts(later))]
        contents = [types.Content(role=role, parts=[types.Part.from_text(text=text)]) for role, text in formatted]
        prompt = (MEMORY_CHANGES_PROMPT + "\n\nNew History Snippet to Analyze:\nThe recent messages above.")
        if missing_text:
            prompt += "\n\nMessages that have just left the recent window, oldest first:\n" + missing_text
        if want_summary:
            prompt += SAVE_SUMMARY_REQUEST
        chat = self.client.chats.create(
            model=model,
            history=contents if contents else None,
            config=types.GenerateContentConfig(system_instruction=system, temperature=0.7,
                                               safety_settings=safety_settings(), tools=_tools(active)),
        )
        return chat.send_message(prompt)

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

    def update_memory_bank(self, idle=False, plain=False, models=None):
        # Rewrite memory.md from the recent history, trying models in order. plain sends the save as ordinary text on the
        # non-DeepSeek models, used for a save before a model switch. models overrides the chain, for a save that has to
        # start on one model: a switch between two DeepSeek models runs it where the anchored history is still cached.
        if not self.full_history:
            self.log("[yellow]No chat history to save yet! (OwO)[/yellow]")
            return

        if models is None:
            models = self._save_models()
        else:
            models = list(models)
        if plain:
            models = [m for m in models if not str(m).startswith(("deepseek:", "openrouter:"))] or models

        # The save covers everything in the chat. An idle save leaves the last IDLE_KEEP_MESSAGES out, so the next message
        # keeps them in its history.
        cutoff = len(self.full_history) - (IDLE_KEEP_MESSAGES if idle else 0)
        start = max(min(self.summary_covered, cutoff), cutoff - SUMMARY_MAX_MESSAGES)
        pending = self.full_history[start:cutoff]        # messages not yet in the summary
        want_summary = cutoff > self.summary_covered
        # each message is read to the length the window keeps it at, so the summary sees what the model saw
        summary_turns = "\n".join(f"{e.get('speaker') or e.get('role', 'user')}: {e.get('text', '')[:CLIP_CHARS]}" for e in pending)
        # The cached save sends the chat's own prompt, which only pays off on a DeepSeek model, and only when it goes to
        # the one whose cache holds that prompt. On the Gemini chain the compact plain save is sent instead.
        cached = self.cache_anchored() and not plain and str(models[0] if models else "").startswith("deepseek:")
        if cached:
            # Messages from the prompt's history start on are already in the save's history, so only the ones before it
            # are sent as text.
            held_from = self.memory_anchor(self.save_base(len(self.full_history)))
            missing = self.full_history[start:min(cutoff, held_from)]
            summary_turns = "\n".join(f"{e.get('speaker') or e.get('role', 'user')}: {e.get('text', '')[:CLIP_CHARS]}" for e in missing)

        for m in models:
            try:
                self._count_request(m)
                self.log(f"[dim magenta]Updating memory.md using {m}... (owo)[/dim magenta]")

                turns = [f"{e['role']}: {e['text'][:300]}" for e in self.full_history[-max(MEMORY_SNIPPET, self.memory_every()):]]
                snippet = self.safe_text("\n".join(turns))

                existing = ""
                if os.path.exists(self.memory_bank_file):
                    try:
                        existing = _read_text(self.memory_bank_file).strip()
                    except Exception:
                        pass

                prompt = MEMORY_CHANGES_PROMPT
                if existing:
                    prompt += f"\n\nExisting Memory Bank (memory.md):\n{existing}"
                prompt += f"\n\nNew History Snippet to Analyze:\n{snippet}"
                if want_summary:
                    prompt += (f"\n\nRunning summary so far (earlier in this chat):\n{self.summary_text or '(none yet)'}"
                               f"\n\nMessages that have just left the recent window, oldest first:\n{self.safe_text(summary_turns)}"
                               "\n\nAfter the memory changes, on its own line write === SUMMARY ===, then the updated running summary: "
                               "plain prose under 250 words covering what happened, decisions made and facts now established. "
                               "Keep the points from the existing summary that still matter.")

                if cached:
                    resp = self._cached_save_call(m, self.safe_text(summary_turns) if summary_turns else "", want_summary)
                else:
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

                # The reply is a list of changes, applied to the bank. Facts it does not mention are kept.
                merged, counts = memory_bank.apply(existing, text)
                with open(self.memory_bank_file, "w", encoding="utf-8") as f:
                    f.write(merged)
                if summary_text:
                    if idle:
                        self.idle_anchor = cutoff
                        self.idle_done = len(self.full_history)
                    self._save_summary(summary_text, cutoff)
                self._add_episodes(cutoff)
                self._build_system_prompt()
                # With the chat set to read tensions on each memory save, the tension pass runs now. Its failure
                # leaves the saved memory bank as it is.
                if self.tension_mode == "save":
                    try:
                        self.tension_pass("save", self._count_request)
                    except Exception as err:
                        self.log(f"[yellow]Tension pass after the memory save failed: {err}[/yellow]")
                    # The pass may have changed the threads that the prompt above was built from. Rebuild, so the prompt
                    # matches the saved record. The next turn rebuilds it again anyway.
                    self._build_system_prompt()
                self.log(f"[dim green]Updated memory.md: {counts['added']} added, {counts['updated']} updated, "
                         f"{counts['removed']} removed.[/dim green]")
                return
            except Exception as e:
                self.log(f"[yellow]Error with model {m}: {e}[/yellow]")
                continue

        if not os.path.exists(self.memory_bank_file):
            with open(self.memory_bank_file, "w", encoding="utf-8") as f:
                f.write("# Memory Bank\n\n## Relationships\nActive roleplay partnership.\n\n## Key Facts\n- Session is active and saved.\n")
            self.log("[green]Created baseline memory.md successfully![/green]")
