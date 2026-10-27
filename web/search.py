# Global search across one account's worlds, Mind Palace entries and saved chats. Character cards are
# searched in web/app.py, where the gallery card helpers live. Matches are accent- and case-insensitive
# and every word in the query must appear.
import json
import os
import unicodedata

from web import branches, palace, worlds

MAX_QUERY = 120
MAX_TERMS = 8
SNIPPET_CHARS = 170
MAX_CHARACTERS = 12
MAX_WORLDS = 8
MAX_PALACE = 12
MAX_CHATS = 24
PER_CHAT = 3


def norm(value):
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(c for c in text if not unicodedata.combining(c))


def terms(query):
    return norm(query).split()[:MAX_TERMS]


def matches(haystack, wanted):
    return bool(wanted) and all(term in haystack for term in wanted)


def snippet(text, wanted):
    text = " ".join(str(text or "").split())
    if not text:
        return ""
    flat = norm(text)
    pos = -1
    for term in wanted:
        pos = flat.find(term)
        if pos >= 0:
            break
    if pos < 0:
        return text[:SNIPPET_CHARS] + ("..." if len(text) > SNIPPET_CHARS else "")
    start = max(0, pos - SNIPPET_CHARS // 3)
    end = min(len(text), start + SNIPPET_CHARS)
    return ("..." if start else "") + text[start:end] + ("..." if end < len(text) else "")


def _read_list(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def world_results(items, wanted, limit=MAX_WORLDS):
    out = []
    for item in items:
        sections = item.get("sections") or {}
        parts = [item.get("name", ""), item.get("summary", "")]
        for key in worlds.SECTIONS:
            parts.append(worlds.SECTION_TITLES.get(key, key))
            parts.extend(sections.get(key) or [])
        if not matches(norm("\n".join(str(p) for p in parts)), wanted):
            continue
        section = ""
        body = item.get("summary", "")
        for key in worlds.SECTIONS:
            text = "\n".join(str(x) for x in (sections.get(key) or []))
            if text and matches(norm(text), wanted):
                section = key
                body = text
                break
        out.append({"id": item.get("id", ""), "name": item.get("name", ""), "summary": item.get("summary", ""),
                    "section": section, "section_title": worlds.SECTION_TITLES.get(section, "") if section else "",
                    "snippet": snippet(body, wanted)})
        if len(out) >= limit:
            break
    return out


def palace_results(user_root, wanted, label_for, limit=MAX_PALACE):
    out = []
    if not os.path.isdir(user_root):
        return out
    for folder in sorted(os.listdir(user_root)):
        pdir = os.path.join(user_root, folder)
        if not os.path.isdir(pdir) or not os.path.isfile(os.path.join(pdir, palace.FILE)):
            continue
        info = label_for(folder)
        if info is None:
            continue
        entries, _cfg = palace.load_all(pdir)
        per_chat = 0
        for entry in entries:
            haystack = "\n".join([entry.get("title", ""), " ".join(entry.get("keys") or []),
                                  " ".join(entry.get("secondary") or []), entry.get("content", "")])
            if not matches(norm(haystack), wanted):
                continue
            out.append({"folder": folder, "chat": info["chat"], "name": info["name"], "kind": info["kind"],
                        "entry_id": entry.get("id", ""), "title": entry.get("title", ""),
                        "snippet": snippet(entry.get("content", ""), wanted)})
            per_chat += 1
            if per_chat >= PER_CHAT or len(out) >= limit:
                break
        if len(out) >= limit:
            break
    return out


def chat_results(user_root, wanted, label_for, limit=MAX_CHATS):
    out = []
    if not os.path.isdir(user_root):
        return out
    for folder in sorted(os.listdir(user_root)):
        state_root = os.path.join(user_root, folder)
        if not os.path.isdir(state_root):
            continue
        info = label_for(folder)
        if info is None:
            continue
        try:
            active = branches.listing(state_root)["active"]
            state_dir = branches.active_dir(state_root)
        except (OSError, ValueError):
            active, state_dir = branches.MAIN, state_root
        messages = _read_list(os.path.join(state_dir, "memory.json"))
        if not messages:
            continue
        per_chat = 0
        for index in range(len(messages) - 1, -1, -1):
            msg = messages[index]
            if not isinstance(msg, dict):
                continue
            text = str(msg.get("text") or "")
            if not text or not matches(norm(text), wanted):
                continue
            out.append({"folder": folder, "chat": info["chat"], "name": info["name"], "kind": info["kind"],
                        "branch": active, "index": index, "role": str(msg.get("role") or ""),
                        "speaker": str(msg.get("speaker") or ""), "ts": str(msg.get("ts") or ""),
                        "snippet": snippet(text, wanted)})
            per_chat += 1
            if per_chat >= PER_CHAT:
                break
        if len(out) >= limit:
            break
    out.sort(key=lambda row: row.get("ts") or "", reverse=True)
    return out[:limit]
