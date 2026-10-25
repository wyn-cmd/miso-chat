# Autonomous dreaming: during a character's quiet hours, the character reviews what happened lately and tidies
# its own memory. It consolidates the memory bank, links facts that were not linked before, and drops automatic
# Mind Palace entries that are duplicates or wrong. Pinned facts and the user's own palace entries are never
# changed. A dream runs once a night, and only after enough new messages.
import datetime
import random
import re

MIN_NEW = 6              # messages since the last dream before another one is worth running
EPISODES_READ = 8        # most recent episodes shown to the dream
DEFAULT_START = 120      # 02:00, in minutes after midnight
DEFAULT_END = 360        # 06:00

SYSTEM = (
    "You are reflecting in a quiet hour, away from the user, as this character or group chat. You are given the memory bank, the "
    "automatic Mind Palace entries (with ids) and some recent moments from the chat. Tidy the memory without losing "
    "anything the chat still supports. Reply with lines in these forms only:\n"
    "ADD: [Section] one new fact, one line\n"
    "UPDATE: the exact existing line => the corrected line\n"
    "REMOVE: the exact existing line, only when it is a duplicate or no longer true\n"
    "CONNECT: a link between two facts already in the bank, written as one line; use this for new connections\n"
    "DROP: the id of an automatic palace entry that duplicates another or is wrong\n"
    "EVENT: actor | what happened | what the character came to believe | what it led to | after: an earlier event id\n"
    "EVOLVE: one short line on how the character has changed because of what happened, only when the chat shows it\n"
    "Write an EVENT only for something that changed the situation or the character, never for small talk, and at most "
    "five. Use no | inside the fields. The after: part is optional. Write EVOLVE lines only for a real change in the "
    "character's outlook or behaviour.\n"
    "NOTE: one sentence on what you tidied or noticed\n"
    "Never touch the user's own palace entries or pinned facts, because they are not shown to you. "
    "Write nothing else. If nothing needs doing, reply with only NOTE: nothing to change."
)


# Whether a minute of the day falls in a window. A window that runs past midnight (22:00 to 04:00) is handled.
def in_window(start, end, minute):
    if start == end:
        return False
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end


# The prompt for one dream: the memory bank, the automatic palace entries and the recent episodes. With event memory
# on, also the messages since the last event pass (new_lines) and the latest events, for the dream to link against.
def build_prompt(memory, auto_entries, recent, new_lines=None, recent_events=None):
    lines = ["Memory bank (memory.md):", memory.strip() or "(empty)", "", "Automatic palace entries:"]
    if not auto_entries:
        lines.append("- none")
    for e in auto_entries:
        lines.append(f"- id {e['id']}: {e['title'] or 'Untitled'}; keys {', '.join(e['keys'])}; content: {e['content']}")
    lines += ["", "Recent moments from the chat:"]
    lines += [f"- {text}" for text in recent] or ["- none"]
    if new_lines is not None:
        lines += ["", "Recent events the character has recorded (for the after: field):"]
        lines += [f"- {text}" for text in recent_events or []] or ["- none"]
        lines += ["", "Messages since the last reflection on events:"]
        lines += new_lines or ["- none"]
    lines += ["", "Reflect now. Reply with the lines only."]
    return "\n".join(lines)


# Splits a dream's reply into memory changes (ADD, UPDATE, REMOVE, with CONNECT becoming an ADD to the Connections
# section), palace drops, and the note. Lines that match none of these are ignored.
def parse(text):
    changes, drops, note = [], [], ""
    for raw in (text or "").splitlines():
        line = raw.strip()
        found = re.match(r"^(ADD|UPDATE|REMOVE|CONNECT|DROP|NOTE)\s*:\s*(.*)$", line, re.I)
        if not found:
            continue
        kind, payload = found.group(1).upper(), found.group(2).strip()
        if kind == "CONNECT":
            changes.append(payload if re.match(r"^\s*\[", payload) else f"ADD: [Connections] {payload}")
        elif kind == "DROP":
            if payload:
                drops.append(payload.split()[0].strip(".,"))
        elif kind == "NOTE":
            note = payload[:300]
        else:
            changes.append(f"{kind}: {payload}")
    return "\n".join(changes), drops, note


# Whether a row is due now: enabled, inside its window in its own time zone, and not already dreamt today.
def due(row, local_now, every_days=1):
    # Due in the quiet hours, once every_days days at the least since the last dream.
    minute = local_now.hour * 60 + local_now.minute
    if not in_window(row["start_min"], row["end_min"], minute):
        return False
    if not row["last_day"]:
        return True
    last = datetime.date.fromisoformat(row["last_day"])
    return (local_now.date() - last).days >= every_days


# Walks every enabled dream. Each one is marked for the day before it runs, so a failed dream is not retried
# all night. fire(uid, character) runs the dream and returns True when it ran.
def order(due_rows):
    # Characters that have never dreamed come first, in no particular order. Then the rest, shuffled, so no
    # character is always last when the server's dream cap runs out.
    first = [r for r in due_rows if not r["last_day"]]
    rest = [r for r in due_rows if r["last_day"]]
    random.shuffle(first)
    random.shuffle(rest)
    return first + rest


def tick(store, fire, zone_for, now, every_days=1, log=lambda m: None):
    due_rows = []
    for row in store.dream_enabled_rows():
        local = now.astimezone(zone_for(row["tz"]))
        if due(row, local, every_days):
            due_rows.append(dict(row, local_day=local.strftime("%Y-%m-%d")))
    for row in order(due_rows):
        uid, name = row["user_id"], row["character"]
        store.dream_update(uid, name, last_day=row["local_day"])
        try:
            if fire(uid, name):
                log(f"dream ran for {name} (user {uid})")
        except Exception as err:   # one failed dream must not stop the others
            log(f"dream for {name} (user {uid}) failed: {err}")
