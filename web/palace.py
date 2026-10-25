# Mind Palace: author-written entries that join the prompt when their keys show up in the recent chat.
# Works like SillyTavern's World Info: primary and secondary keys with AND/NOT logic, always-on
# entries, a scan depth per entry, an insertion order, a size budget that drops the lowest order
# first, and optional recursion (a matched entry can bring in another). Plain keyword matching, so
# it costs no model calls and no requests.
import functools
import hashlib
import json
import os
import re
import secrets
import unicodedata

FILE = "palace.json"
MAX_ENTRIES = 60
MAX_KEYS = 10
KEY_MAX = 60
TITLE_MAX = 80
CONTENT_MAX = 2000
DEPTH_MAX = 30           # the most recent messages any entry may scan
DEFAULT_DEPTH = 6
DEFAULT_BUDGET = 3000    # characters of matched entries added to one prompt
BUDGET_RANGE = (500, 12000)
MAX_MATCHED = 24
RECURSION_STEPS = 3
LOGICS = ("and_any", "and_all", "not_any", "not_all")
# Automatic entries: the character adds them from the chat (see palace_auto.py). Off unless the user turns it on.
AUTO_DEFAULT = {"enabled": False, "every": 3, "limit": 20}
AUTO_EVERY_RANGE = (1, 10)
AUTO_LIMIT_RANGE = (1, MAX_ENTRIES)


class PalaceError(ValueError):
    pass


def _text(value, limit, what):
    if value is None:
        return ""
    if not isinstance(value, str):
        raise PalaceError(f"{what} must be text.")
    value = value.strip()
    if len(value) > limit:
        raise PalaceError(f"{what} must be {limit} characters or fewer.")
    return value


def _flag(value, default, what):
    if value is None:
        return default
    if not isinstance(value, bool):
        raise PalaceError(f"{what} must be on or off.")
    return value


def _int(value, low, high, default, what):
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        raise PalaceError(f"{what} must be a whole number.")
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise PalaceError(f"{what} must be a whole number.") from None
    if not low <= n <= high:
        raise PalaceError(f"{what} must be from {low} to {high}.")
    return n


def _keys(value, what, limit):
    if value is None:
        return []
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list):
        raise PalaceError(f"{what} must be a list, or comma-separated text.")
    out, seen = [], set()
    for key in value:
        key = _text(key, KEY_MAX, "A key")
        if key and key.lower() not in seen:
            seen.add(key.lower())
            out.append(key)
    if len(out) > limit:
        raise PalaceError(f"{what} can have up to {limit} keys.")
    return out


def new_id():
    return secrets.token_hex(4)


def validate(raw):
    # Strict: the editor shows these messages. Raises PalaceError for the first problem found.
    if not isinstance(raw, list):
        raise PalaceError("Mind Palace entries must be a list.")
    if len(raw) > MAX_ENTRIES:
        raise PalaceError(f"A character can have up to {MAX_ENTRIES} Mind Palace entries.")
    out = []
    for item in raw:
        if not isinstance(item, dict):
            raise PalaceError("Each Mind Palace entry needs a title, keys and content.")
        title = _text(item.get("title"), TITLE_MAX, "A title")
        label = title or "An entry"
        content = _text(item.get("content"), CONTENT_MAX, "Entry content")
        keys = _keys(item.get("keys"), f"'{label}'", MAX_KEYS)
        secondary = _keys(item.get("secondary"), f"'{label}' secondary keys", MAX_KEYS)
        constant = _flag(item.get("constant"), False, f"'{label}' always on")
        if not constant and not keys:
            raise PalaceError(f"'{label}' needs at least one key, or set it to always on.")
        if not content:
            raise PalaceError(f"'{label}' needs content.")
        logic = item.get("logic") or "and_any"
        if logic not in LOGICS:
            raise PalaceError(f"'{label}' logic must be one of: {', '.join(LOGICS)}.")
        entry_id = item.get("id") if isinstance(item.get("id"), str) and re.fullmatch(r"[0-9a-f]{8}", item.get("id")) else ""
        out.append({
            "id": entry_id,
            "auto": _flag(item.get("auto"), False, f"'{label}' automatic"),
            "title": title,
            "keys": keys,
            "secondary": secondary,
            "logic": logic,
            "content": content,
            "constant": constant,
            "enabled": _flag(item.get("enabled"), True, f"'{label}' enabled"),
            "whole": _flag(item.get("whole"), True, f"'{label}' whole words"),
            "depth": _int(item.get("depth"), 1, DEPTH_MAX, DEFAULT_DEPTH, f"'{label}' scan depth"),
            "order": _int(item.get("order"), 0, 1000, 100, f"'{label}' order"),
        })
    return out


def _auto_settings(data, base):
    # Missing values keep what is saved. The automatic-entry switch and its limits are validated like the rest.
    if not isinstance(data, dict):
        data = {}
    return {
        "enabled": _flag(data.get("enabled"), base["enabled"], "Automatic entries"),
        "every": _int(data.get("every"), AUTO_EVERY_RANGE[0], AUTO_EVERY_RANGE[1], base["every"],
                      "The check interval"),
        "limit": _int(data.get("limit"), AUTO_LIMIT_RANGE[0], AUTO_LIMIT_RANGE[1], base["limit"],
                      "The automatic entry limit"),
    }


def validate_settings(data, base=None):
    # base holds the saved settings; a value left out of data keeps its saved value.
    if not isinstance(data, dict):
        data = {}
    base = base or {"recursive": False, "budget": DEFAULT_BUDGET, "auto": dict(AUTO_DEFAULT), "episodes": True}
    return {
        "recursive": _flag(data.get("recursive"), base["recursive"], "Recursive scanning"),
        "budget": _int(data.get("budget"), BUDGET_RANGE[0], BUDGET_RANGE[1], base["budget"],
                       "The character budget"),
        "auto": _auto_settings(data.get("auto"), base["auto"]),
        "episodes": _flag(data.get("episodes"), base.get("episodes", True), "Episodic memory"),
    }


def load_all(folder):
    # Reads leniently: a damaged file means no entries, not a broken chat.
    path = os.path.join(folder, FILE)
    if not os.path.isfile(path):
        return [], validate_settings({})
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return [], validate_settings({})
        return validate(data.get("entries", [])), validate_settings(data)
    except (OSError, ValueError, PalaceError):
        return [], validate_settings({})


def load(folder):
    return load_all(folder)[0]


def copy_starting(src_folder, dst_folder):
    # First use copies the character's starting palace into the account's own folder. Later edits stay there.
    src = os.path.join(src_folder, FILE)
    dst = os.path.join(dst_folder, FILE)
    if os.path.exists(dst) or not os.path.isfile(src):
        return
    os.makedirs(dst_folder, exist_ok=True)
    with open(src, "rb") as f:
        data = f.read()
    tmp = dst + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, dst)


def save(folder, raw, settings=None):
    entries = validate(raw)
    cfg = validate_settings(settings, base=load_all(folder)[1])
    path = os.path.join(folder, FILE)
    tmp = path + ".tmp"
    payload = {"entries": entries, "recursive": cfg["recursive"], "budget": cfg["budget"], "auto": cfg["auto"],
               "episodes": cfg["episodes"]}
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return entries, cfg






def _norm(text):
    # Case-insensitive and accent-blind, so "cafe" finds "Café".
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c)).casefold()


# The forms of an English word that a whole-word key should also match: plurals, past and -ing forms, -er
# agent nouns, and the spelling changes they need (love/loving, city/cities, stop/stopped). Only these endings
# are added, so "vesper" does not match "vespertine" and "cat" does not match "category".
def _inflections(word):
    forms = {word}
    # -er only on longer words: "cat" must not reach "cater", "hat" must not reach "hatter".
    agent = ("er", "ers") if len(word) >= 4 else ()
    if word.endswith("e") and len(word) > 2:
        stem = word[:-1]
        forms |= {stem + x for x in ("ing", "ed", "s") + agent}
    elif word.endswith("y") and len(word) > 2 and word[-2] not in "aeiou":
        stem = word[:-1]
        forms |= {stem + x for x in ("ies", "ied", "ily")}
    else:
        forms |= {word + x for x in ("s", "es", "ed", "ing") + agent}
        # Doubled final consonant after one vowel: stop -> stopped, run -> running.
        if len(word) >= 3 and re.search(r"[^aeiouwxy][aeiou][^aeiouwxy]$", word):
            forms |= {word + word[-1] + x for x in ("ed", "ing", "s") + agent}
    return forms


@functools.lru_cache(maxsize=4096)
def _word_pattern(key):
    words = key.split()
    last = words[-1]
    if re.fullmatch(r"[a-z0-9]+", last):
        alternatives = sorted(_inflections(last), key=len, reverse=True)
        tail = "(?:" + "|".join(re.escape(f) for f in alternatives) + ")"
    else:
        tail = re.escape(last)
    head = re.escape(" ".join(words[:-1]) + " ") if len(words) > 1 else ""
    return re.compile(r"(?<![a-z0-9])" + head + tail + r"(?![a-z0-9])")


def _pattern(key, whole):
    k = _norm(key)
    if whole and re.fullmatch(r"[a-z0-9 '\-]+", k):
        # Plain words match whole words, with their common endings, so "Vesper" does not hit "Vespertine".
        return _word_pattern(k)
    # Other scripts and symbols (Japanese, "mrrp~") match as plain substrings.
    return re.compile(re.escape(k))


# A key written as "mouse|mice" matches either form. Use it for irregular words the endings cannot reach.
def _any_form(keys, text, whole):
    return any(_pattern(form.strip(), whole).search(text) for key in keys for form in key.split("|") if form.strip())


def count_hits(keys, texts, whole=True):
    # How many of the recent messages contain any of these keys. Used to rank candidate entries by how often
    # their keys come up in the rolling window.
    pattern_keys = [k for k in keys if k.strip()]
    return sum(1 for t in texts if _any_form(pattern_keys, _norm(str(t)), whole))


def _fires(entry, text):
    # text is normalised scan text. Always-on entries fire without a key.
    if entry["constant"]:
        return True
    if not _any_form(entry["keys"], text, entry["whole"]):
        return False
    if not entry["secondary"]:
        return True
    found = [_any_form([k], text, entry["whole"]) for k in entry["secondary"]]
    logic = entry["logic"]
    if logic == "and_any":
        return any(found)
    if logic == "and_all":
        return all(found)
    if logic == "not_any":
        return not any(found)
    return not all(found)  # not_all


# The text one entry adds to the prompt.
def block_for(entry):
    return f"{entry['title']}\n{entry['content']}" if entry["title"] else entry["content"]


# The entries a turn's prompt keeps, so the caller can score them. weights maps an entry's retention key to a rank
# multiplier; an entry with no weight is ranked exactly as before. The user's own entries are never weighted here.
def match_entries(entries, texts, budget=DEFAULT_BUDGET, recursive=False, weights=None):
    texts = [str(t) for t in texts if t]
    scans = {}

    def scan(depth):
        if depth not in scans:
            scans[depth] = "\n".join(_norm(t) for t in texts[-depth:])
        return scans[depth]

    active = [e for e in entries if e["enabled"]]
    chosen = [e for e in active if _fires(e, scan(e["depth"]))]
    if recursive and chosen:
        chosen_ids = {id(e) for e in chosen}
        pool = "\n".join(_norm(e["content"]) for e in chosen)
        for _ in range(RECURSION_STEPS):
            new = [e for e in active if id(e) not in chosen_ids and _fires(e, pool)]
            if not new:
                break
            chosen += new
            chosen_ids.update(id(e) for e in new)
            pool = "\n".join(_norm(e["content"]) for e in new)

    # Highest order first, so the budget keeps the most important entries. A weight scales the order, so a memory
    # that has proved useful keeps its place against one that has not. A weight is never applied to an entry the
    # user wrote, and is clamped to at most 1.0, so a weight can only ever demote a memory the character wrote: an
    # author's own entry always ranks at or above an automatic entry of the same order.
    def rank(e):
        weight = 1.0
        if weights and e.get("auto"):
            weight = max(0.0, min(1.0, float(weights.get(palace_key(e), 1.0) or 1.0)))
        return -e["order"] * weight

    chosen.sort(key=rank)
    kept, used = [], 0
    for e in chosen:
        block = block_for(e)
        if used + len(block) > budget:
            continue
        kept.append(e)
        used += len(block)
        if len(kept) >= MAX_MATCHED:
            break
    # Lower order first in the prompt, so higher order lands nearer the end (as in SillyTavern).
    kept.sort(key=lambda e: e["order"])
    return kept


def match(entries, texts, budget=DEFAULT_BUDGET, recursive=False, weights=None):
    # texts: recent messages, oldest first, the message being sent last. Each entry scans only its own depth.
    # Returns the matched entries' text. When the budget runs out, the lowest order is dropped first.
    return [block_for(e) for e in match_entries(entries, texts, budget, recursive, weights)]


# The key the caller scores an entry under: its id, or a hash of its content when it has none.
def palace_key(entry):
    entry_id = str(entry.get("id") or "").strip()
    if entry_id:
        return "p:" + entry_id
    return "p:" + hashlib.sha1(str(entry.get("content") or "").encode("utf-8")).hexdigest()[:10]
