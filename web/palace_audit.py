# Mind Palace upkeep: which entries fire, which ones crowd the prompt, which ones nothing has used, and names that point
# at nothing. The counts live in palace_audit.json beside palace.json. They change only when a reply is recorded or a
# core flag is set, so opening the check never changes an entry. The name check is a heuristic: it lists candidates from
# capitals and the lore text, and the author decides what they mean.
import json
import os
import re
import time

from web import palace

FILE = "palace_audit.json"
WINDOW_TURNS = 200       # an entry never called in this many messages...
UNUSED_DAYS = 14         # ...and not used for this many days is orphaned, unless it is core memory
RECENT_TURNS = 50        # the window the over-firing rate is measured over
OVER_FIRING = 0.5        # fires in at least this share of the recent messages is over-firing
MIN_RATE_TURNS = 20      # the rate is judged only once this many messages have been seen
NAME_LIMIT = 12          # candidate names shown per entry
DAY = 86400
KEY_RE = re.compile(r"p:[0-9a-f]{8,10}")
# A capitalised run of words. A run at the start of a sentence is trimmed, because capitals there say nothing.
CAP_RUN = re.compile(r"[A-Z][A-Za-z'’\-]*(?:[ \t]+[A-Z][A-Za-z'’\-]*)*")
SENTENCE_END = set(".!?\n\"“”:;(")
# Capitalised words that are not names: sentence openers, pronouns, days and months, and titles.
COMMON = {
    "A", "An", "And", "As", "At", "But", "By", "For", "From", "If", "In", "Into", "Is", "It", "Its", "Of", "On",
    "Or", "So", "To", "With", "Yes", "No", "Not", "Now", "Then", "There", "Here", "This", "That", "These", "Those",
    "I", "He", "She", "They", "We", "You", "Him", "Her", "Them", "Us", "Me", "My", "Your", "Our", "Their", "His",
    "Who", "What", "Where", "When", "Why", "How", "Which", "While", "Because", "Although", "Though", "After",
    "Before", "Once", "Still", "Even", "Just", "Only", "Also", "Each", "Every", "Some", "Many", "Most", "All", "Any",
    "Nothing", "Something", "Everything", "Nobody", "Someone", "Everyone", "Maybe", "Perhaps", "Please", "Thanks",
    "Hello", "Hi", "Oh", "Well", "Okay", "Sure", "Look", "Listen", "Remember", "Never", "Always", "Sometimes",
    "Often", "Today", "Tonight", "Tomorrow", "Yesterday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
    "Saturday", "Sunday", "January", "February", "March", "April", "May", "June", "July", "August", "September",
    "October", "November", "December", "Mr", "Mrs", "Ms", "Dr", "Sir", "Madam", "Captain", "Chapter", "Part",
}


COUNT_KEY = {"never_triggered": "never_triggered", "orphaned": "orphaned", "over_firing": "over_firing",
             "dead_reference": "dead_references"}


class AuditError(ValueError):
    pass


def _path(pdir):
    return os.path.join(pdir, FILE)


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _record(raw, turns, now):
    # One entry's counts, as read. A damaged record is replaced with a fresh one that starts now.
    if not isinstance(raw, dict):
        raw = {}
    last_turn = raw.get("last_turn")
    recent = raw.get("recent") if isinstance(raw.get("recent"), list) else []
    return {
        "born": _int(raw.get("born"), turns),
        "born_at": _number(raw.get("born_at")) or now,
        "hits": max(0, _int(raw.get("hits"))),
        "last_turn": _int(last_turn, None) if last_turn is not None else None,
        "last_used": _number(raw.get("last_used")),
        "recent": [_int(t, 0) for t in recent if _int(t, -1) >= 0][-RECENT_TURNS:],
        "core": raw.get("core") is True,
    }


def _load(pdir, now):
    # Reads leniently: a damaged file starts again from no counts, never a broken chat.
    try:
        with open(_path(pdir), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    turns = max(0, _int(data.get("turns")))
    raw_entries = data.get("entries") if isinstance(data.get("entries"), dict) else {}
    entries = {k: _record(v, turns, now) for k, v in raw_entries.items() if KEY_RE.fullmatch(str(k))}
    return {"turns": turns, "entries": entries}


def _save(pdir, state):
    os.makedirs(pdir, exist_ok=True)
    path = _path(pdir)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def record_turn(pdir, fired, now=None):
    # Called once for each message the user sends, after the reply is kept. fired holds the palace keys of the
    # entries whose keys matched this turn (palace.match_entries, before the budget cut). Entries that are not in the
    # palace any more are left alone, so a damaged palace file cannot wipe the counts.
    entries, _cfg = palace.load_all(pdir)
    if not entries:
        return
    now = time.time() if now is None else now
    state = _load(pdir, now)
    state["turns"] += 1
    turn = state["turns"]
    fired = set(fired)
    for entry in entries:
        key = palace.palace_key(entry)
        # Born just before this message, so the message that first shows the entry counts toward the window.
        rec = state["entries"].setdefault(key, _record({}, turn - 1, now))
        if key in fired:
            rec["hits"] += 1
            rec["last_turn"] = turn
            rec["last_used"] = now
            rec["recent"] = [t for t in rec["recent"] if t > turn - RECENT_TURNS] + [turn]
    _save(pdir, state)


def set_core(pdir, key, core, now=None):
    # Marks or unmarks an entry as core memory. Core entries are never reported as orphaned, and an automatic core
    # entry is never forgotten by the retention pass.
    if not isinstance(key, str) or not KEY_RE.fullmatch(key):
        raise AuditError("That entry is not in this palace.")
    if not isinstance(core, bool):
        raise AuditError("Core memory must be on or off.")
    now = time.time() if now is None else now
    state = _load(pdir, now)
    rec = state["entries"].setdefault(key, _record({}, state["turns"], now))
    rec["core"] = core
    _save(pdir, state)


def core_keys(pdir):
    # The palace keys marked as core memory. Empty when nothing is marked or the file is missing.
    state = _load(pdir, time.time())
    return {k for k, v in state["entries"].items() if v["core"]}


def _norm(text):
    return palace._norm(text)


def _candidates(text):
    # Capitalised names that sit inside a sentence. A single capital at a sentence start is skipped: it may just be the
    # first word of the sentence.
    found = []
    for match in CAP_RUN.finditer(text):
        words = match.group(0).split()
        before = text[:match.start()].rstrip(" \t")
        if not before or before[-1] in SENTENCE_END:
            words = words[1:]
        while words and words[0] in COMMON:
            words = words[1:]
        while words and words[-1] in COMMON:
            words = words[:-1]
        if words:
            found.append(" ".join(words))
    return found


def _mentioned(phrase, corpus):
    # True when the phrase, or its first word, appears as whole words in any of the lore texts.
    words = phrase.split()
    options = [phrase] if len(words) == 1 else [phrase, words[0]]
    for option in options:
        pattern = re.compile(r"(?<![a-z0-9])" + re.escape(_norm(option)) + r"(?![a-z0-9])")
        if any(pattern.search(text) for text in corpus):
            return True
    return False


def _dead_references(entry, others, lore):
    # Names in this entry that no other entry, character, persona, world or the character's own card mentions.
    own = {_norm(entry.get("title") or "")}
    own |= {_norm(k) for k in entry["keys"] + entry["secondary"]}
    corpus = [_norm(t) for t in others + lore if t]
    refs, seen = [], set()
    for name in _candidates(entry["content"]):
        norm = _norm(name)
        if norm in seen or norm in own or len(norm) < 2:
            continue
        seen.add(norm)
        if not _mentioned(name, corpus):
            refs.append(name)
        if len(refs) >= NAME_LIMIT:
            break
    return refs


def _entry_text(entry):
    return " ".join([entry.get("title") or "", " ".join(entry["keys"] + entry["secondary"]), entry["content"]])


def audit(pdir, entries, lore=(), now=None):
    # The upkeep view for one palace. lore is a list of texts the account already has (characters, personas, worlds and
    # the character's own card); names found there are not reported. Every entry's own text is checked against the
    # other entries too, so a name that another entry explains is not a dead reference.
    now = time.time() if now is None else now
    state = _load(pdir, now)
    turns = state["turns"]
    lore = [str(t) for t in lore if t]
    rows = []
    counts = {"entries": len(entries), "orphaned": 0, "never_triggered": 0, "over_firing": 0, "dead_references": 0,
              "core": 0}
    for entry in entries:
        key = palace.palace_key(entry)
        rec = state["entries"].get(key) or _record({}, turns, now)
        seen = max(0, turns - rec["born"])
        if rec["last_turn"] is not None:
            turns_unused = turns - rec["last_turn"]
        else:
            turns_unused = seen
        since = rec["last_used"] if rec["last_used"] is not None else rec["born_at"]
        days_unused = max(0.0, (now - since) / DAY)
        rate = None
        if seen >= MIN_RATE_TURNS:
            recent = [t for t in rec["recent"] if t > turns - RECENT_TURNS]
            rate = len(recent) / min(RECENT_TURNS, seen)
        others = [_entry_text(e) for e in entries if e is not entry]
        refs = _dead_references(entry, others, lore) if entry["enabled"] else []
        block = palace.block_for(entry)
        flags = []
        if entry["enabled"]:
            if rec["hits"] == 0 and seen >= WINDOW_TURNS:
                flags.append("never_triggered")
            if not rec["core"] and turns_unused >= WINDOW_TURNS and days_unused >= UNUSED_DAYS:
                flags.append("orphaned")
            if not entry["constant"] and rate is not None and rate >= OVER_FIRING:
                flags.append("over_firing")
            if refs:
                flags.append("dead_reference")
        for flag in flags:
            counts[COUNT_KEY[flag]] += 1
        if rec["core"]:
            counts["core"] += 1
        rows.append({
            "key": key,
            "id": entry.get("id") or "",
            "title": entry["title"] or entry["content"][:40],
            "preview": entry["content"][:120],
            "enabled": entry["enabled"],
            "constant": entry["constant"],
            "flags": flags,
            "core": rec["core"],
            "hits": rec["hits"],
            "seen": seen,
            "turns_unused": turns_unused,
            "days_unused": round(days_unused, 1),
            "rate": None if rate is None else round(rate * 100),
            "chars": len(block),
            "per_reply_chars": None if rate is None else round(rate * len(block)),
            "refs": refs,
        })
    return {
        "turns": turns,
        "limits": {"window_turns": WINDOW_TURNS, "unused_days": UNUSED_DAYS, "recent_turns": RECENT_TURNS,
                   "over_firing_percent": round(OVER_FIRING * 100)},
        "counts": counts,
        "entries": rows,
    }
