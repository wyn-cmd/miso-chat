# Off-screen life: what a character did between chat sessions. A director pass asks the model for one or two
# short fragments and, sometimes, a twist card. The newest ones join the prompt near the end, so the character's
# life keeps moving while nobody is writing. Nothing here runs or costs anything until an admin turns the server
# switch on and the chat's own switch is on too.
import json
import os
import re

FILE = "offscreen.json"
MAX_FRAGMENTS = 12       # kept per chat; the oldest are dropped past this
MAX_TWISTS = 8
FRAGMENT_MAX = 400
TWIST_MAX = 300
PROMPT_FRAGMENTS = 3     # newest fragments sent with a reply
PROMPT_TWISTS = 1        # newest twist sent with a reply
NEW_MESSAGES = 20        # recent messages shown to the director

SYSTEM = (
    "You write a character's off-screen life between chat sessions, in the character's voice and world. "
    "Reply with lines in these forms only:\n"
    "FRAGMENT: one short sentence about what the character did, saw or felt since the last session. Keep it small "
    "and in keeping with the card and the recent chat. Do not decide anything the user would decide, and do not "
    "speak or act for the user.\n"
    "TWIST: one short plot hook or complication that could surface in a later chat, only when it grows naturally "
    "from the story. At most one TWIST, and often none.\n"
    "Write one or two FRAGMENT lines. Write nothing else."
)


def empty():
    return {"fragments": [], "twists": []}


# The stored record for a chat. A missing or damaged file reads as an empty one.
def load(folder):
    record = empty()
    try:
        with open(os.path.join(folder, FILE), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return record
    if not isinstance(data, dict):
        return record
    for key, cap in (("fragments", MAX_FRAGMENTS), ("twists", MAX_TWISTS)):
        items = data.get(key)
        if isinstance(items, list):
            record[key] = [e for e in items if isinstance(e, dict) and isinstance(e.get("text"), str)][-cap:]
    return record


# Writes the record through a temporary file, so a crash cannot leave half a record behind.
def save(folder, record):
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, FILE)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


# Splits a director reply into fragments and twists. Lines that match neither are ignored.
def parse(text):
    fragments, twists = [], []
    for raw in (text or "").splitlines():
        found = re.match(r"^(FRAGMENT|TWIST)\s*:\s*(.+)$", raw.strip(), re.I)
        if not found:
            continue
        kind, body = found.group(1).upper(), found.group(2).strip()
        if not body:
            continue
        if kind == "FRAGMENT" and len(fragments) < 2:
            fragments.append(body[:FRAGMENT_MAX])
        elif kind == "TWIST" and not twists:
            twists.append(body[:TWIST_MAX])
    return fragments, twists


# Adds one pass to the record, newest last, dropping the oldest past the caps.
def apply(record, fragments, twists, day):
    for text in fragments:
        record["fragments"].append({"day": day, "text": text[:FRAGMENT_MAX]})
    for text in twists:
        record["twists"].append({"day": day, "text": text[:TWIST_MAX]})
    record["fragments"] = record["fragments"][-MAX_FRAGMENTS:]
    record["twists"] = record["twists"][-MAX_TWISTS:]
    return record


# The newest fragments and one twist for the end of the prompt. Empty when there is nothing to add.
def prompt_block(record):
    fragments = record.get("fragments") or []
    twists = record.get("twists") or []
    if not fragments and not twists:
        return ""
    lines = ["[While the user was away, off-screen. These are things the character did or felt, not things the "
             "user saw.]"]
    lines += [f"- {item['text']}" for item in fragments[-PROMPT_FRAGMENTS:]]
    if twists:
        lines += ["", "A twist that may surface when it fits the scene:"]
        lines += [f"- {item['text']}" for item in twists[-PROMPT_TWISTS:]]
    return "\n".join(lines)


# The prompt for one director pass: the card, the recent chat, the moments already written and, when the chat
# tracks them, its open narrative threads. A twist can grow from a thread without settling it.
def build_prompt(card, recent, record, threads=""):
    lines = ["The character card:", (card or "(none)").strip()[:2000], "", "Recent messages from the chat:"]
    lines += recent or ["- none"]
    lines += ["", "Off-screen moments already written (do not repeat them):"]
    lines += [f"- {item['text']}" for item in (record.get("fragments") or [])[-PROMPT_FRAGMENTS:]] or ["- none"]
    if threads:
        lines += ["", threads.strip()]
    lines += ["", "Write the off-screen pass now. Reply with the lines only."]
    return "\n".join(lines)
