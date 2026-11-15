# Imports lorebooks written for SillyTavern World Info, JanitorAI and character cards as Mind Palace
# entries. The formats share one shape at heart: entries with trigger keys and content. Field names
# differ between exporters, so each one is read from the names it uses and mapped onto the palace's
# own entry, which stores only what the palace can actually use.
import json
import re

from web import palace

MAX_BYTES = 5 * 1024 * 1024
SELECTIVE_LOGIC = {0: "and_any", 1: "not_all", 2: "not_any", 3: "and_all"}


class LorebookError(ValueError):
    pass


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _split(value):
    # A key list or a string with commas or newlines, as both exporters write it.
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"[,\n]", value) if part.strip()]
    if isinstance(value, list):
        return [part.strip() for part in value if isinstance(part, str) and part.strip()]
    return []


def _first(entry, names, limit=palace.KEY_MAX):
    for name in names:
        value = entry.get(name)
        if isinstance(value, str):
            value = value.strip()
            if value:
                return value[:limit]
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item.strip():
                    return item.strip()[:limit]
    return ""


def _keys(entry, names):
    for name in names:
        keys = _split(entry.get(name))
        if keys:
            return [key[:palace.KEY_MAX] for key in keys[:palace.MAX_KEYS]]
    return []


def _content(entry):
    for name in ("content", "entry", "text", "value"):
        value = _text(entry.get(name))
        if value:
            return value[:palace.CONTENT_MAX]
    return ""


def _enabled(entry):
    if isinstance(entry.get("enabled"), bool):
        return entry["enabled"]
    if isinstance(entry.get("disable"), bool):
        return not entry["disable"]
    if isinstance(entry.get("disabled"), bool):
        return not entry["disabled"]
    return True


def _order(entry):
    for name in ("order", "insertion_order", "insertionOrder"):
        try:
            value = int(entry.get(name))
        except (TypeError, ValueError):
            continue
        return max(0, min(1000, value))
    # JanitorAI's priority runs the other way: a higher priority should survive the budget.
    try:
        priority = int(entry.get("priority"))
    except (TypeError, ValueError):
        return 100
    return max(0, min(1000, 100 - priority * 10))


def _depth(entry):
    for name in ("depth", "scanDepth"):
        try:
            value = int(entry.get(name))
        except (TypeError, ValueError):
            continue
        return max(1, min(palace.DEPTH_MAX, value))
    return palace.DEFAULT_DEPTH


def _logic(entry, secondary):
    # A card that is not selective ignores its secondary keys. Anything unknown falls back to AND ANY.
    if entry.get("selective") is False or not secondary:
        return "and_any"
    try:
        return SELECTIVE_LOGIC.get(int(entry.get("selectiveLogic", 0)), "and_any")
    except (TypeError, ValueError):
        return "and_any"


def _whole(entry):
    value = entry.get("matchWholeWords")
    if isinstance(value, bool):
        return value
    value = entry.get("whole")
    return value if isinstance(value, bool) else True


def _to_entry(raw):
    # One exporter entry as a palace entry, or None when it has nothing the palace can hold.
    keys = _keys(raw, ("key", "keys", "keywords", "keysRaw", "keywordsRaw"))
    secondary = _keys(raw, ("keysecondary", "secondary_keys", "secondaryKeys", "secondary", "keysSecondary"))
    content = _content(raw)
    title = (_first(raw, ("comment", "name", "title", "label")) or (keys[0] if keys else ""))[:palace.TITLE_MAX]
    constant = bool(raw.get("constant") or raw.get("always") or raw.get("alwaysOn"))
    if not content or (not keys and not constant):
        return None
    return {
        "title": title,
        "keys": keys,
        "secondary": secondary,
        "logic": _logic(raw, secondary),
        "content": content,
        "constant": constant,
        "enabled": _enabled(raw),
        "whole": _whole(raw),
        "depth": _depth(raw),
        "order": _order(raw),
    }


def _entries_from(data):
    # Every entry object in a lorebook export, whichever wrapper the exporter used.
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        raise LorebookError("That file is not a lorebook.")
    book = data
    if isinstance(data.get("character_book"), dict):
        book = data["character_book"]
    elif isinstance(data.get("data"), dict) and isinstance(data["data"].get("character_book"), dict):
        book = data["data"]["character_book"]
    elif isinstance(data.get("lorebook"), dict):
        book = data["lorebook"]
    elif isinstance(data.get("lorebook"), list):
        return [item for item in data["lorebook"] if isinstance(item, dict)]
    entries = None
    if isinstance(book, dict):
        for name in ("entries", "items", "entry"):
            if isinstance(book.get(name), (dict, list)):
                entries = book[name]
                break
    if isinstance(entries, dict):
        return [item for item in entries.values() if isinstance(item, dict)]
    if isinstance(entries, list):
        return [item for item in entries if isinstance(item, dict)]
    # A bare map of entry objects, keyed by id or index.
    values = [item for item in book.values() if isinstance(item, dict)]
    if any(_content(item) or _split(item.get("key") or item.get("keys")) for item in values):
        return values
    raise LorebookError("That file does not hold any lorebook entries.")


def parse(data):
    # (palace entries, skipped count, notes) from a decoded lorebook. Nothing is saved here.
    raw_entries = _entries_from(data)
    entries, skipped = [], 0
    probability = False
    for raw in raw_entries:
        if len(entries) >= palace.MAX_ENTRIES:
            skipped += 1
            continue
        item = _to_entry(raw)
        if item is None:
            skipped += 1
            continue
        try:
            entries.extend(palace.validate([item]))
        except palace.PalaceError:
            skipped += 1
            continue
        if raw.get("useProbability") or raw.get("probability") is not None:
            probability = True
    notes = [f"{len(entries)} lorebook entries were added to the Mind Palace."]
    if skipped:
        notes.append(f"{skipped} entries were skipped because they had no keys, no content, or could not fit.")
    if probability:
        notes.append("Probabilities and insertion positions are not used by the Mind Palace; imported entries fire when their keys match.")
    return entries, skipped, notes


def parse_bytes(raw):
    if len(raw) > MAX_BYTES:
        raise LorebookError("That lorebook is over 5 MB.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise LorebookError("That file is not UTF-8 text.") from None
    try:
        data = json.loads(text)
    except ValueError:
        raise LorebookError("That file is not a lorebook JSON file.") from None
    return parse(data)
