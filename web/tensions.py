# Narrative tension tracking: the open threads of a story that have not been resolved yet. A secret nobody has
# revealed, a promise nobody has kept, a contradiction between two characters' accounts. Each one is kept as an
# unresolved constraint and put in the prompt until the story earns a resolution, so the drama builds slowly instead
# of closing every thread as soon as it opens.
#
# The model reports tensions with TENSION lines and closes them with RESOLVE lines. Where that happens is chosen per
# character: during the nightly dream, or on every memory bank save. This module only parses, stores and renders them.
# It does no model calls. It reads and writes one file, tensions.json, in the character's own folder.
import json
import os
import re
import tempfile

FILE = "tensions.json"
KINDS = ("secret", "promise", "contradiction")
MAX_OPEN = 12            # open tensions kept; the oldest resolved ones go first, open ones are never dropped silently
MAX_RESOLVED = 30        # resolved tensions kept as a short history
MAX_NEW = 4              # most TENSION lines one pass may add
PROMPT_OPEN = 6          # open tensions shown in one turn's prompt
FIELD_MAX = 240
NEW_MESSAGES = 40        # most messages since the last pass that one pass reads
MODES = ("off", "dream", "save")
DEFAULT_MODE = "off"


# An empty record, or the stored one with any missing parts filled in. covered is the history length already read.
def empty():
    return {"next": 0, "covered": 0, "items": []}


# Reads a character's tensions. A damaged file means an empty record, not a broken chat.
def load(path):
    record = empty()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return record
    if not isinstance(data, dict):
        return record
    record["next"] = int(data.get("next", 0) or 0)
    record["covered"] = int(data.get("covered", 0) or 0)
    record["items"] = [i for i in data.get("items", []) if _valid(i)]
    return record


# Writes the record atomically, so a crash mid-write keeps the last good file.
def save(path, record):
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix="tensions-", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# Whether a stored item has the fields the rest of the module relies on.
def _valid(item):
    return (isinstance(item, dict) and isinstance(item.get("id"), str) and item.get("kind") in KINDS
            and isinstance(item.get("text"), str) and item.get("text")
            and item.get("status") in ("open", "resolved"))


# Reads TENSION and RESOLVE lines from a reply. A TENSION line is
#   TENSION: kind | what is left open | who is involved
# where kind is secret, promise or contradiction and the last field is optional. A RESOLVE line is
#   RESOLVE: t3 | how it was resolved
# Anything else is ignored.
def parse(text):
    opened, resolved = [], []
    for raw in (text or "").splitlines():
        found = re.match(r"^(TENSION|RESOLVE)\s*:\s*(.*)$", raw.strip(), re.I)
        if not found:
            continue
        kind, payload = found.group(1).upper(), found.group(2).strip()
        parts = [p.strip() for p in payload.split("|")]
        if kind == "RESOLVE":
            if len(parts) >= 2 and parts[0] and parts[1]:
                resolved.append({"id": parts[0].lower(), "how": parts[1][:FIELD_MAX]})
            continue
        if len(opened) >= MAX_NEW or len(parts) < 2:
            continue
        label = parts[0].lower()
        if label not in KINDS or not parts[1]:
            continue
        who = parts[2][:120] if len(parts) > 2 else ""
        opened.append({"kind": label, "text": parts[1][:FIELD_MAX], "who": who})
    return opened, resolved


# Applies a pass's new and resolved tensions. Each new one gets the next id and the turn it opened on. A RESOLVE for
# an id that is not open is ignored. Returns (opened, resolved) counts.
def apply(record, opened, resolved, turn):
    added = 0
    for item in opened:
        if any(i["status"] == "open" and i["text"] == item["text"] for i in record["items"]):
            continue
        record["next"] += 1
        record["items"].append({"id": f"t{record['next']}", "kind": item["kind"], "text": item["text"],
                                "who": item["who"], "status": "open", "opened": turn, "how": ""})
        added += 1
    done = 0
    for item in resolved:
        for i in record["items"]:
            if i["id"] == item["id"] and i["status"] == "open":
                i["status"], i["how"], i["closed"] = "resolved", item["how"], turn
                done += 1
    _trim(record)
    return added, done


# Keeps open tensions (up to MAX_OPEN, the oldest open ones are the ones that have waited longest and stay) and the
# latest resolved history. Nothing open is dropped to make room for a new one; the new one is refused instead.
def _trim(record):
    opened = [i for i in record["items"] if i["status"] == "open"]
    closed = [i for i in record["items"] if i["status"] == "resolved"]
    record["items"] = opened[:MAX_OPEN] + closed[-MAX_RESOLVED:]


# The tensions still open, oldest first. Used by the settings page and the dream.
def open_items(record):
    return [i for i in record["items"] if i["status"] == "open"]


# The prompt for one dream or save pass: the open tensions with ids, and the recent messages to read. Messages are
# passed in by the caller, already cut to the window it chose.
def build_prompt(record, recent_lines):
    lines = ["Open tensions (with ids):"]
    current = open_items(record)
    if not current:
        lines.append("- none")
    for item in current:
        who = f" ({item['who']})" if item["who"] else ""
        lines.append(f"- id {item['id']}: {item['kind']}{who}: {item['text']}")
    lines += ["", "Recent messages:"]
    lines += [f"- {text}" for text in recent_lines] or ["- none"]
    lines += ["", "Reply with TENSION and RESOLVE lines only, or nothing if there is nothing new."]
    return "\n".join(lines)


# The system instruction for the pass. It asks for the same line forms parse reads, and for restraint: a tension is
# only resolved when the chat actually resolves it.
SYSTEM = (
    "You track the open narrative tensions of a story. A tension is a secret not yet revealed, a promise not yet kept, "
    "or a contradiction between two characters' accounts. Reply with lines in these forms only:\n"
    "TENSION: kind | what is left open, in one short sentence | who is involved\n"
    "RESOLVE: id | how it was resolved, in one short sentence\n"
    "kind is secret, promise or contradiction. Open a tension only when the chat sets one up. Resolve a tension only when "
    "the chat itself resolves it on the page; a hint is not a resolution. Use at most four TENSION lines and no | inside "
    "the fields. Write nothing else."
)


# The block put in front of the next reply. The open tensions are constraints: the character must not resolve them
# early, reveal a secret unless the scene earns it, or act as though a broken promise was kept.
def prompt_block(record):
    current = open_items(record)[:PROMPT_OPEN]
    if not current:
        return ""
    lines = ["Open threads in this story (unresolved). Keep them open until the story earns a resolution. Do not "
             "reveal a secret, keep or break a promise, or settle a contradiction unless the scene has built to it:"]
    for item in current:
        who = f" ({item['who']})" if item["who"] else ""
        lines.append(f"- {item['kind']}{who}: {item['text']}")
    return "\n".join(lines)


# The messages since the last pass, as short lines for the model. Only the newest NEW_MESSAGES are read, so one pass
# stays small even after a long gap.
def new_lines(record, history):
    span = history[record["covered"]:][-NEW_MESSAGES:]
    return [f"- [{e.get('speaker') or e.get('role', 'user')}]: {str(e['text'])[:400]}" for e in span if e.get("text")]
