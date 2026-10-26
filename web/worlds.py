# Worlds: the setting a story takes place in. Each world holds locations, magic systems, rules, civilisations and other
# notes. An account keeps its own worlds, private to it, in one file in its own folder. A novel picks one world, and a
# private chat may pick one too; the account can also set a default world for chats that have none of their own.
#
# This module is pure logic plus its own file. It does no model calls. A world is rendered as a block for the system
# prompt, so the rules stay in front of the model on every turn, the way the character card does.
import json
import os
import re
import secrets
import tempfile

FILE = "worlds.json"
PREFIX = "wld-"
ID_RE = re.compile(r"wld-[0-9a-f]{8}")
SECTIONS = ("locations", "magic", "rules", "civilisations", "factions", "technology", "other")
SECTION_TITLES = {"locations": "Locations", "magic": "Magic systems", "rules": "Rules",
                  "civilisations": "Civilisations", "factions": "Factions", "technology": "Technology",
                  "other": "Other details"}
NO_WORLD = "none"        # a chat or novel that is set explicitly to have no world, not the account default
MAX_WORLDS = 20
NAME_MAX = 60
SUMMARY_TOKENS = 250     # estimated tokens in a world's summary
GUARD_CHARS = 8000       # a damaged file cannot bring in more than this for one entry
ENTRY_TOKENS = 300       # estimated tokens in one entry
# estimated tokens in one section, the sum of its entries; Other details is kept shorter
SECTION_TOKENS = {"locations": 1500, "magic": 1500, "rules": 1500, "civilisations": 1500, "factions": 1500,
                  "technology": 1500, "other": 1500}
ENTRIES_MAX = 30         # entries in one section
# A line may be typed with a bullet (-, * or a dot) or a [NEW] tag from a rules list. The bullet and tag are dropped on save,
# because the prompt adds its own "- ". A number ("1.", "2)") is kept, since it can matter when a rule is cited.
NEW_TAG = re.compile(r"\s*\[new\]", re.IGNORECASE)
BULLET = re.compile(r"^[-*•]\s+")
NUMBERED = re.compile(r"^\d+[.)]\s")
BLOCK_TOKENS = 10000     # estimated tokens the world block may take in a prompt; the rest is cut at a whole entry


class WorldError(ValueError):
    pass


# An empty store for one account.
def empty():
    return {"default": None, "items": []}


# Reads an account's worlds. A damaged file means no worlds, not a broken chat.
def load(path):
    store = empty()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return store
    if not isinstance(data, dict):
        return store
    store["items"] = [_clean_item(i) for i in data.get("items", []) if _valid_id(i)]
    default = data.get("default")
    store["default"] = default if any(i["id"] == default for i in store["items"]) else None
    return store


# Writes the store atomically, so a crash mid-write keeps the last good file.
def save(path, store):
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix="worlds-", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _valid_id(item):
    return isinstance(item, dict) and isinstance(item.get("id"), str) and ID_RE.fullmatch(item["id"]) is not None


# Keeps only the known sections and their text, so a hand-edited or older file cannot inject odd keys.
def _clean_item(item):
    sections = {}
    raw = item.get("sections") if isinstance(item.get("sections"), dict) else {}
    for key in SECTIONS:
        entries = raw.get(key) if isinstance(raw.get(key), list) else []
        kept = [normalise_entry(str(e))[:GUARD_CHARS] for e in entries]
        sections[key] = [e for e in kept if e][:ENTRIES_MAX]
    return {"id": item["id"], "name": clean_text(item.get("name"), NAME_MAX) or "World",
            "summary": clean_text(item.get("summary"), GUARD_CHARS), "sections": sections,
            "created": str(item.get("created") or "")}


# One line of text, with whitespace squeezed and an optional length cap.
def clean_text(value, limit=None):
    text = " ".join(str(value or "").split())
    return text if limit is None else text[:limit]


# One entry as it is stored: squeezed, with any [NEW] tag and leading bullet removed. The settings page counts the same
# text, so its count matches what is saved.
def normalise_entry(value):
    text = clean_text(value)
    text = NEW_TAG.sub("", text)
    text = BULLET.sub("", text)
    return text.strip()


# Estimated tokens for a piece of text, at about four characters a token. This is the same rule as Engine.estimate_tokens
# in rp_core.py and the counter on the settings page, so both sides agree. It is an estimate, not a tokenizer's count.
def estimate_tokens(text):
    return (len(text) + 3) // 4


# Checks a world the user sent. Sections arrive as lists of entries, one entry per line of the form the user typed.
def _check(name, summary, sections):
    name = clean_text(name, NAME_MAX)
    if not name:
        raise WorldError("Give the world a name.")
    if not isinstance(sections, dict):
        raise WorldError("Sections must be a set of lists.")
    clean = {}
    for key in SECTIONS:
        entries = sections.get(key) or []
        if not isinstance(entries, list) or not all(isinstance(e, str) for e in entries):
            raise WorldError(f"{SECTION_TITLES[key]} must be a list of entries.")
        kept = [normalise_entry(e) for e in entries]
        kept = [e for e in kept if e]
        if len(kept) > ENTRIES_MAX:
            raise WorldError(f"{SECTION_TITLES[key]} can hold up to {ENTRIES_MAX} entries.")
        # refused, not cut, so the user sees what was not saved
        for e in kept:
            cost = estimate_tokens(e)
            if cost > ENTRY_TOKENS:
                raise WorldError(f"An entry in {SECTION_TITLES[key]} is about {cost} tokens. One entry can be "
                                 f"up to {ENTRY_TOKENS}. Shorten it or split it in two.")
        total = sum(estimate_tokens(e) for e in kept)
        if total > SECTION_TOKENS[key]:
            raise WorldError(f"{SECTION_TITLES[key]} is about {total} tokens. A section can be up to "
                             f"{SECTION_TOKENS[key]}. Shorten an entry or move some to another section.")
        clean[key] = kept
    summary = clean_text(summary)
    # refused, not cut, so the user sees what was not saved
    if estimate_tokens(summary) > SUMMARY_TOKENS:
        raise WorldError(f"The summary is about {estimate_tokens(summary)} tokens. It can be up to {SUMMARY_TOKENS}. "
                         f"Shorten it.")
    return name, summary, clean


def get(store, wid):
    for item in store["items"]:
        if item["id"] == wid:
            return item
    return None


# Creates a world and returns it. The store is changed in place; the caller saves it.
def create(store, name, summary, sections, now_iso):
    if len(store["items"]) >= MAX_WORLDS:
        raise WorldError(f"An account can keep up to {MAX_WORLDS} worlds. Delete one first.")
    name, summary, clean = _check(name, summary, sections)
    wid = PREFIX + secrets.token_hex(4)
    while get(store, wid) is not None:
        wid = PREFIX + secrets.token_hex(4)
    item = {"id": wid, "name": name, "summary": summary, "sections": clean, "created": now_iso}
    store["items"].append(item)
    return item


# Replaces a world's name, summary and sections. Its id and creation time are kept.
def update(store, wid, name, summary, sections):
    item = get(store, wid)
    if item is None:
        raise WorldError("That world no longer exists.")
    name, summary, clean = _check(name, summary, sections)
    item.update({"name": name, "summary": summary, "sections": clean})
    return item


# Removes a world. If it was the default, the account has no default afterwards.
def delete(store, wid):
    if get(store, wid) is None:
        raise WorldError("That world no longer exists.")
    store["items"] = [i for i in store["items"] if i["id"] != wid]
    if store["default"] == wid:
        store["default"] = None


# Sets the account's default world, or clears it with None.
def set_default(store, wid):
    if wid is not None and get(store, wid) is None:
        raise WorldError("That world no longer exists.")
    store["default"] = wid


# The world a chat should use: its own choice if it has one, else the account default, else None. A chat's choice
# that no longer exists falls back to the default rather than failing.
def world_for(store, chat_choice):
    if chat_choice == NO_WORLD:
        return None
    if chat_choice and get(store, chat_choice) is not None:
        return get(store, chat_choice)
    if store["default"]:
        return get(store, store["default"])
    return None


# The block for the system prompt. Sections keep their order and entries keep the order they were written in. When
# the whole text would pass BLOCK_TOKENS, the rest is cut at a whole entry, so nothing is cut mid-sentence.
def prompt_block(item):
    if item is None:
        return ""
    lines = [f"The world this story is set in: {item['name']}."]
    if item["summary"]:
        lines.append(item["summary"])
    used = sum(estimate_tokens(line) for line in lines)
    for key in SECTIONS:
        entries = item["sections"].get(key) or []
        if not entries:
            continue
        head = f"{SECTION_TITLES[key]}:"
        if used + estimate_tokens(head) > BLOCK_TOKENS:
            break
        lines.append(head)
        used += estimate_tokens(head)
        for entry in entries:
            # a numbered entry keeps its number and gets no dash; anything else is a list item
            line = entry if NUMBERED.match(entry) else f"- {entry}"
            cost = estimate_tokens(line)
            if used + cost > BLOCK_TOKENS:
                return "\n".join(lines)
            lines.append(line)
            used += cost
    return "\n".join(lines)
