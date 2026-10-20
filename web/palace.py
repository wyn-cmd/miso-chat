# Mind Palace: author-written entries that join the prompt only when one of their keys shows up in
# the recent chat. Plain keyword matching, so it costs no model calls and no requests.
import json
import os
import re
import unicodedata

FILE = "palace.json"
MAX_ENTRIES = 40
MAX_KEYS = 10
KEY_MAX = 60
TITLE_MAX = 80
CONTENT_MAX = 2000
SCAN_MESSAGES = 6     # the newest messages searched, including the one being sent
BUDGET = 3000         # characters of matched entries added to one prompt
MAX_MATCHED = 6


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
        content = _text(item.get("content"), CONTENT_MAX, "Entry content")
        keys_raw = item.get("keys", [])
        if isinstance(keys_raw, str):
            keys_raw = keys_raw.split(",")
        if not isinstance(keys_raw, list):
            raise PalaceError("Keys must be a list, or comma-separated text.")
        keys, seen = [], set()
        for key in keys_raw:
            key = _text(key, KEY_MAX, "A key")
            if key and key.lower() not in seen:
                seen.add(key.lower())
                keys.append(key)
        label = title or "An entry"
        if not keys:
            raise PalaceError(f"'{label}' needs at least one key.")
        if len(keys) > MAX_KEYS:
            raise PalaceError(f"'{label}' can have up to {MAX_KEYS} keys.")
        if not content:
            raise PalaceError(f"'{label}' needs content.")
        out.append({"title": title, "keys": keys, "content": content})
    return out


def load(folder):
    # Reads leniently: a damaged file means no entries, not a broken chat.
    path = os.path.join(folder, FILE)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return validate(data.get("entries", []) if isinstance(data, dict) else [])
    except (OSError, ValueError, PalaceError):
        return []


def save(folder, raw):
    entries = validate(raw)
    path = os.path.join(folder, FILE)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"entries": entries}, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return entries


def _norm(text):
    # Case-insensitive and accent-blind, so "cafe" finds "Café".
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c)).casefold()


def _pattern(key):
    k = _norm(key)
    if re.fullmatch(r"[a-z0-9 '\-]+", k):
        # Plain words match whole words only, so "Vesper" does not hit "Vespers" by accident.
        return re.compile(r"(?<![a-z0-9])" + re.escape(k) + r"(?![a-z0-9])")
    # Other scripts and symbols (Japanese, "mrrp~") match as plain substrings.
    return re.compile(re.escape(k))


def match(entries, texts):
    # Returns the matched entries' text, in the order they were written, within the size budget.
    hay = [_norm(t) for t in texts if t]
    chosen, used = [], 0
    for entry in entries:
        patterns = [_pattern(k) for k in entry["keys"]]
        if not any(p.search(h) for p in patterns for h in hay):
            continue
        block = f"{entry['title']}\n{entry['content']}" if entry["title"] else entry["content"]
        if used + len(block) > BUDGET:
            continue
        chosen.append(block)
        used += len(block)
        if len(chosen) >= MAX_MATCHED:
            break
    return chosen
