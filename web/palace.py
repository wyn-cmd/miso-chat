# Mind Palace: author-written entries that join the prompt when their keys show up in the recent chat.
# Works like SillyTavern's World Info: primary and secondary keys with AND/NOT logic, always-on
# entries, a scan depth per entry, an insertion order, a size budget that drops the lowest order
# first, and optional recursion (a matched entry can bring in another). Plain keyword matching, so
# it costs no model calls and no requests.
import json
import os
import re
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
        out.append({
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


def validate_settings(data):
    if not isinstance(data, dict):
        data = {}
    return {
        "recursive": _flag(data.get("recursive"), False, "Recursive scanning"),
        "budget": _int(data.get("budget"), BUDGET_RANGE[0], BUDGET_RANGE[1], DEFAULT_BUDGET,
                       "The character budget"),
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
    cfg = load_all(folder)[1] if settings is None else validate_settings(settings)
    path = os.path.join(folder, FILE)
    tmp = path + ".tmp"
    payload = {"entries": entries, "recursive": cfg["recursive"], "budget": cfg["budget"]}
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return entries, cfg






def _norm(text):
    # Case-insensitive and accent-blind, so "cafe" finds "Café".
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c)).casefold()


def _pattern(key, whole):
    k = _norm(key)
    if whole and re.fullmatch(r"[a-z0-9 '\-]+", k):
        # Plain words match whole words only, so "Vesper" does not hit "Vespers" by accident.
        return re.compile(r"(?<![a-z0-9])" + re.escape(k) + r"(?![a-z0-9])")
    # Other scripts and symbols (Japanese, "mrrp~") match as plain substrings.
    return re.compile(re.escape(k))


def _fires(entry, text):
    # text is normalised scan text. Always-on entries fire without a key.
    if entry["constant"]:
        return True
    if not any(_pattern(k, entry["whole"]).search(text) for k in entry["keys"]):
        return False
    if not entry["secondary"]:
        return True
    found = [bool(_pattern(k, entry["whole"]).search(text)) for k in entry["secondary"]]
    logic = entry["logic"]
    if logic == "and_any":
        return any(found)
    if logic == "and_all":
        return all(found)
    if logic == "not_any":
        return not any(found)
    return not all(found)  # not_all


def match(entries, texts, budget=DEFAULT_BUDGET, recursive=False):
    # texts: recent messages, oldest first, the message being sent last. Each entry scans only its own depth.
    # Returns the matched entries' text. When the budget runs out, the lowest order is dropped first.
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

    # Highest order first, so the budget keeps the most important entries.
    chosen.sort(key=lambda e: -e["order"])
    kept, used = [], 0
    for e in chosen:
        block = f"{e['title']}\n{e['content']}" if e["title"] else e["content"]
        if used + len(block) > budget:
            continue
        kept.append((e["order"], block))
        used += len(block)
        if len(kept) >= MAX_MATCHED:
            break
    # Lower order first in the prompt, so higher order lands nearer the end (as in SillyTavern).
    kept.sort(key=lambda kb: kb[0])
    return [block for _, block in kept]
