# The story bible: a wiki page for a chat, built from everything the character remembers. It lists the
# people the story introduced, the places visited, the items picked up, what is still unresolved and the
# key events, with a short overview on top. Nothing here touches memory.md or the palace; the bible is
# written beside them in the chat's state folder and is only read, so it is safe to rebuild at any time.
import json
import os
import re

FILE = "bible.json"

MIN_HISTORY = 4          # an empty chat has nothing to write yet
OPENING_READ = 30        # first messages shown to the model, so the origins are covered
RECENT_READ = 30         # last messages shown to the model, so nothing new is missed
MESSAGE_CLIP = 400       # each message is clipped to this many characters in the prompt
PINNED_CLIP = 4000
SUMMARY_CLIP = 4000
PALACE_CONTENT_CLIP = 400
OVERVIEW_MAX = 1500
LIST_MAX = 40            # most entries kept in one section
ITEM_MAX = 300           # longest text kept for one entry of a section
SECTIONS = ("characters_met", "places_visited", "items_acquired", "open_questions", "key_events")

SYSTEM = (
    "You write a story bible: a short wiki page about a roleplay chat, for the reader. You are given everything "
    "the character or group remembers (the memory bank, pinned facts, Mind Palace entries, the running summary, "
    "story threads and recorded events) and the first and latest messages of the chat. Write plainly and factually "
    "about what happened in the story, never in character. Reply with JSON only, and nothing outside it:\n"
    "{\"overview\": \"2 to 5 sentences: what this story is about so far,\",\n"
    " \"characters_met\": [\"one person, creature or faction the story introduced, with a few words about them\", ...],\n"
    " \"places_visited\": [\"one place the story visited or mentioned meaningfully\", ...],\n"
    " \"items_acquired\": [\"one significant object gained, lost, given or mentioned\", ...],\n"
    " \"open_questions\": [\"one thing the story left unresolved or hinted at\", ...],\n"
    " \"key_events\": [\"one important thing that happened, in one sentence\", ...]}\n"
    "Work only from what the sources support; do not invent. Skip a list's entries you cannot support. Keep each "
    "entry under 300 characters and the overview under 1500. Do not name the reader unless their persona name is "
    "in the sources. An empty list is fine. Use no Markdown headings inside the JSON."
)


# Skeleton of a record, used when a section is missing from a model reply.
def empty():
    return {"when": "", "history": 0, "overview": "",
            "characters_met": [], "places_visited": [], "items_acquired": [],
            "open_questions": [], "key_events": []}


def load(path):
    data = _read(path)
    return data if data is not None else None


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return normalise(data) if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def save(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def normalise(data):
    # Coerce a parsed reply (or a damaged file) into the saved shape: known keys, capped lengths, strings only.
    out = empty()
    out["when"] = str(data.get("when") or "")
    out["history"] = _count(data.get("history"))
    overview = _clean(data.get("overview"), OVERVIEW_MAX)
    out["overview"] = re.sub(r"^#+\s*", "", overview)
    for key in SECTIONS:
        out[key] = _list(data.get(key), LIST_MAX, ITEM_MAX)
    return out


def _count(value):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _clean(value, limit):
    if not isinstance(value, str):
        return ""
    return value.strip()[:limit]


def _list(value, count_limit, item_limit):
    if isinstance(value, str):
        value = value.splitlines()
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        item = _clean(item, item_limit)
        if item:
            out.append(item)
    return out[:count_limit]


# The prompt for one generation: every memory source plus the first and latest messages, so the model can
# see the story from its start to where it stands now.
def build_prompt(memory, pinned, palace_entries, summary, tensions, events,
                 opening, recent, speakers):
    lines = ["Sources (facts worth trusting: memory bank, pinned facts, Mind Palace, running summary,",
             "open story threads, recorded events):"]
    lines += ["", "Memory bank (memory.md):", memory.strip() or "(empty)"]
    lines += ["", "Pinned facts:", pinned.strip()[:PINNED_CLIP] or "(none)"]
    lines += ["", "Mind Palace entries:"]
    if not palace_entries:
        lines.append("- none")
    for e in palace_entries:
        label = f"- {e['title'] or 'Untitled'}"
        if e.get("keys"):
            label += f" (keys: {', '.join(e['keys'])})"
        lines.append(f"{label}: {str(e.get('content') or '')[:PALACE_CONTENT_CLIP]}")
    lines += ["", "Running summary of the story so far:", summary.strip()[:SUMMARY_CLIP] or "(none yet)"]
    lines += ["", "Open story threads:"]
    lines += [f"- {t}" for t in tensions] or ["- none recorded"]
    lines += ["", "Recorded events:"]
    lines += [f"- {e}" for e in events] or ["- none recorded"]
    lines += ["", f"First {OPENING_READ} messages of the chat ({speakers} speaking):"]
    lines += opening or ["- none"]
    lines += ["", f"Last {RECENT_READ} messages of the chat:"]
    lines += recent or ["- none"]
    lines += ["", "Write the story bible now. Reply with the JSON only."]
    return "\n".join(lines)


def opening_lines(history):
    return _messages(history[:OPENING_READ])


def recent_lines(history):
    return _messages(history[-RECENT_READ:])


def _messages(lines):
    out = []
    for e in lines:
        if not isinstance(e, dict):
            continue
        text = str(e.get("text") or "").strip()[:MESSAGE_CLIP]
        if not text:
            continue
        who = e.get("speaker") or ("You" if e.get("role") == "user" else "Model")
        out.append(f"- {who}: {text}")
    return out[:OPENING_READ + RECENT_READ]


# Pulls the JSON object out of a model reply: fenced or bare, with anything else around it ignored.
# Returns the JSON parsed, or None when no parsable object is found.
def extract_json(text):
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


# Validates a parsed reply and returns it in the saved shape. A reply without the structure
# is refused with None, so a bad generation never overwrites a good bible.
def parse(text):
    data = extract_json(text)
    if data is None:
        return None
    if not isinstance(data.get("overview"), (str, type(None))):
        return None
    return normalise(data)


def with_stamp(data, when, history_len):
    data["when"] = when
    data["history"] = max(0, int(history_len))
    return data
