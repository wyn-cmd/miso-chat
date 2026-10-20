# Character profile cards: the headline, summary and tags users see on the home page.
# Kept in profile.json beside character.md, so the roleplay prompt never carries them.

import collections
import json
import os
import threading
import time
import unicodedata
from datetime import datetime, timezone

from google.genai import types

from rp_core import MEMORY_MODELS_HEAD, MEMORY_MODELS_TAIL, _text, safety_settings

HEADLINE_MAX = 120
INFO_MAX = 2000
TAGS_MAX = 8
TAG_MAX = 24
CARD_MAX = 20000
PROFILE_FILE = "profile.json"
RETRY_SECONDS = 600     # wait this long before trying a character again after a failure
GEN_MODELS = MEMORY_MODELS_HEAD + MEMORY_MODELS_TAIL
PROFILE_RPM = 14           # profile calls allowed in any rolling minute; the user's limit is under 15
PROFILE_RESERVE = 4        # slots kept free for admin Auto-fill, so background writes never block it
RATE_WINDOW = 60.0
RATE_MARGIN = 0.5          # wait a little past the window so a slot is never counted early

PROFILE_SYSTEM = (
    "You are the CHARACTER PROFILE WRITER. You write the short profile card that a user reads on a "
    "character's page before starting a chat. The reader has never met the character.\n\n"
    "Reply with one JSON object and nothing else, in this shape:\n"
    "{\"headline\": \"...\", \"info\": \"...\", \"tags\": [\"...\", \"...\"]}\n\n"
    "headline: one sentence, 120 characters at most, on one line. It is the first thing the reader sees.\n"
    "info: a summary of 80 to 180 words in plain prose, with no headings and no lists. Say who the "
    "character is, how they tend to act, and what a chat with them is like. Do not quote the card.\n"
    "tags: 3 to 6 short tags, each one to three words, lower case, such as 'shy', 'night owl', 'mystery'.\n\n"
    "Keep secrets out of all three fields. The card often holds things the reader is not meant to learn yet: "
    "hidden backstory, secret identities, plot twists, hidden motives, true allegiances, notes for the owner, "
    "and content notes. Leave all of that out. Describe only what a person would see or learn by chatting with "
    "the character for a while. If the secret is the most striking thing about the character, describe the "
    "visible side instead and do not hint at the secret.\n\n"
    "Write about the character as a person. Do not describe formatting rules, the card itself, or the "
    "writing process."
)


class ProfileError(ValueError):
    pass


class ProfileBusy(ProfileError):
    # The rate gate had no room within the wait the caller allowed.
    pass


class RateGate:
    # Rolling window: at most `limit` calls in any RATE_WINDOW seconds. Callers block in wait().
    def __init__(self, limit, reserve=0, clock=time.time, sleep=time.sleep):
        self.limit = limit
        self.reserve = reserve   # slots only priority callers may use
        self.clock = clock
        self.sleep = sleep
        self.stamps = collections.deque()
        self.lock = threading.Lock()
        self.priority_waiting = 0   # admin requests queued; background writing pauses while this is above zero

    def wait(self, timeout=None, priority=False):
        # Takes one slot. Returns False rather than waiting past `timeout` seconds.
        # Background callers (priority=False) yield to priority callers such as the admin Auto-fill.
        deadline = None if timeout is None else self.clock() + timeout
        if priority:
            with self.lock:
                self.priority_waiting += 1
        try:
            return self._take(deadline, priority)
        finally:
            if priority:
                with self.lock:
                    self.priority_waiting -= 1

    def _take(self, deadline, priority):
        while True:
            with self.lock:
                now = self.clock()
                yielding = not priority and self.priority_waiting > 0
                if not yielding:
                    while self.stamps and now - self.stamps[0] >= RATE_WINDOW:
                        self.stamps.popleft()
                    cap = self.limit if priority else self.limit - self.reserve
                    if len(self.stamps) < cap:
                        self.stamps.append(now)
                        return True
                    wait_for = RATE_WINDOW + RATE_MARGIN - (now - self.stamps[0])
            if yielding:
                self.sleep(0.5)   # let the priority caller take the next free slot
                continue
            if deadline is not None and self.clock() + wait_for > deadline:
                return False
            self.sleep(wait_for)


def profile_path(folder):
    return os.path.join(folder, PROFILE_FILE)


_SAVE_LOCK = threading.Lock()


def read_profile(folder):
    # The saved profile, or None when this character has never had one saved.
    path = profile_path(folder)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    tags = data.get("tags") or []
    return {"headline": str(data.get("headline") or ""), "info": str(data.get("info") or ""),
            "tags": [str(t) for t in tags if str(t).strip()], "auto": bool(data.get("auto", False))}


def clean_profile(headline, info, tags):
    # Checks typed text and returns the stored form. Raises ProfileError with a message for the admin.
    headline = (headline or "").strip()
    if not headline:
        raise ProfileError("Add a headline, or use Auto-fill.")
    if "\n" in headline or "\r" in headline:
        raise ProfileError("The headline must be one line.")
    if len(headline) > HEADLINE_MAX:
        raise ProfileError(f"The headline must be {HEADLINE_MAX} characters or fewer.")
    info = (info or "").strip()
    if not info:
        raise ProfileError("Add a summary, or use Auto-fill.")
    if len(info) > INFO_MAX:
        raise ProfileError(f"The summary must be {INFO_MAX} characters or fewer.")
    return {"headline": headline, "info": info, "tags": parse_tags(tags)}


def fold_accents(text):
    # Drops accents from Latin letters only, so pokémon matches pokemon. A combining mark after a
    # kana (ド is ト plus a voicing mark) is part of the letter and must stay.
    out, latin = [], False
    for c in unicodedata.normalize("NFD", text):
        if unicodedata.combining(c):
            if latin:
                continue
        else:
            latin = unicodedata.name(c, "").startswith("LATIN")
        out.append(c)
    return unicodedata.normalize("NFC", "".join(out))


def parse_tags(raw):
    # Accepts a comma-separated string or a list. Drops blanks and repeats. Tags are stored lower case
    # so 'Dragon' and 'dragon' gather the same characters.
    if raw is None:
        items = []
    elif isinstance(raw, str):
        items = raw.split(",")
    elif isinstance(raw, list):
        items = raw
    else:
        raise ProfileError("Tags must be a list or comma-separated text.")
    out, seen = [], set()
    for item in items:
        tag = " ".join(str(item).split()).lower()
        tag = fold_accents(tag)   # pokémon = pokemon; Japanese voiced kana stay whole
        if not tag:
            continue
        if len(tag) > TAG_MAX:
            raise ProfileError(f"Each tag must be {TAG_MAX} characters or fewer.")
        if tag.lower() in seen:
            continue
        seen.add(tag.lower())
        out.append(tag)
    if len(out) > TAGS_MAX:
        raise ProfileError(f"Use up to {TAGS_MAX} tags.")
    return out


def save_profile(folder, profile, auto=False):
    # Writes profile.json. auto=True marks text the model wrote, so the admin can tell it apart.
    data = {"headline": profile["headline"], "info": profile["info"], "tags": profile["tags"],
            "auto": bool(auto), "updated": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    path = profile_path(folder)
    with _SAVE_LOCK:
        os.makedirs(folder, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)


def save_if_absent(folder, profile, auto=True):
    # Background writes must not overwrite a profile the admin saved while the model was thinking.
    with _SAVE_LOCK:
        if read_profile(folder) is not None:
            return False
    save_profile(folder, profile, auto=auto)
    return True


def _cut(text, limit):
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:")
    return cut + "." if cut else text[:limit]


def _parse(raw):
    # The model is asked for JSON; tolerate a code fence or a little text around it.
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ProfileError("the model did not return a profile")
    try:
        data = json.loads(raw[start:end + 1])
    except ValueError as err:
        raise ProfileError(f"the profile was not valid JSON ({err})") from None
    headline = " ".join(str(data.get("headline") or "").split())
    info = str(data.get("info") or "").strip()
    tags = data.get("tags") or []
    if not isinstance(tags, list):
        tags = []
    tags = [" ".join(str(t).split())[:TAG_MAX] for t in tags][:TAGS_MAX]
    return clean_profile(_cut(headline, HEADLINE_MAX), _cut(info, INFO_MAX), tags)


def card_prompt(card, known_tags=None):
    text = "Character card:\n" + card[:CARD_MAX] + "\n\nWrite the profile now as one JSON object."
    if known_tags:
        text += ("\n\nTags already in use. Reuse one where it fits, and add a new tag only when none fits: "
                 + ", ".join(known_tags[:150]) + ".")
    return text


def tag_counts(root, folders):
    # Tag name and how many characters use it, most used first. Feeds the editor's suggestions.
    counts = {}
    for folder in folders:
        prof = read_profile(os.path.join(root, folder))
        for tag in (prof or {}).get("tags", []):
            counts[tag] = counts.get(tag, 0) + 1
    return sorted(({"name": k, "count": v} for k, v in counts.items()), key=lambda x: (-x["count"], x["name"]))


def generate(client, card, on_call=None, gate=None, wait_timeout=None, priority=False, known_tags=None):
    # One call per attempt, falling through the model list. Each attempt takes a rate slot first,
    # retries included, so the per-minute limit holds even when a model fails.
    last = None
    for model in GEN_MODELS:
        if gate is not None and not gate.wait(wait_timeout, priority=priority):
            raise ProfileBusy("the profile writer is busy")
        try:
            if on_call is not None:
                on_call(model)
            chat = client.chats.create(
                model=model,
                config=types.GenerateContentConfig(system_instruction=PROFILE_SYSTEM, temperature=0.7,
                                                   safety_settings=safety_settings()),
            )
            return _parse(_text(chat.send_message(card_prompt(card, known_tags))) or "")
        except Exception as err:
            last = err
    raise ProfileError(f"no model could write the profile ({last})")


class ProfileWriter:
    # Fills in missing profiles one at a time in a background thread, so the home page never waits.

    def __init__(self, root, client, gate=None, log=lambda m: None):
        self.root = root
        self.client = client
        self.gate = gate or RateGate(PROFILE_RPM, reserve=PROFILE_RESERVE)
        self.log = log
        self._lock = threading.Lock()
        self._queue = []          # (folder, user_id) waiting
        self._busy = set()        # folders being written now
        self._failed = {}         # folder -> time of last failure
        self._running = False
        self.on_call = None       # set by the app: records one request against a user
        self.tag_source = None    # set by the app: returns the tags already in use

    def pending(self):
        with self._lock:
            return set(self._busy) | {f for f, _ in self._queue}

    def want(self, folders, user_id):
        # Queue the characters that have no profile yet. Safe to call on every page load.
        with self._lock:
            for folder in folders:
                if folder in self._busy or any(f == folder for f, _ in self._queue):
                    continue
                if time.time() - self._failed.get(folder, 0) < RETRY_SECONDS:
                    continue
                self._queue.append((folder, user_id))
            if self._queue and not self._running:
                self._running = True
                threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while True:
            with self._lock:
                if not self._queue:
                    self._running = False
                    return
                folder, user_id = self._queue.pop(0)
                self._busy.add(folder)
            try:
                path = os.path.join(self.root, folder)
                if read_profile(path) is None:
                    with open(os.path.join(path, "character.md"), encoding="utf-8", errors="replace") as f:
                        card = f.read()
                    on_call = (lambda model, uid=user_id: self.on_call(uid, model)) if self.on_call else None
                    known = self.tag_source() if self.tag_source else None
                    profile = generate(self.client, card, on_call=on_call, gate=self.gate, priority=False,
                                       known_tags=known)
                    save_if_absent(path, profile, auto=True)
                    self.log(f"[profile] wrote profile for {folder}")
            except Exception as err:
                with self._lock:
                    self._failed[folder] = time.time()
                self.log(f"[profile] could not write profile for {folder}: {err}")
            finally:
                with self._lock:
                    self._busy.discard(folder)
