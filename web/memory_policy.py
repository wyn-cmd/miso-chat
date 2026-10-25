# Learned memory retention: an outcome-weighted policy for the memories the character extracts for itself.
#
# The idea is adapted from MemPO (self-memory policy optimization): a memory earns its place by being useful, not by
# being old or by being kept. This module is the scoring half of that idea, without any training. No model is fitted
# and no weights are learned by gradient; what is tracked is a running, outcome-weighted estimate per memory, which
# is what the code can honestly support offline. All the numbers below are heuristics, not a trained policy.
#
# What it does. Only the memories the character chose for itself are scored: automatic Mind Palace entries and
# episodes. Entries the user wrote are never scored, ranked or dropped. Each memory has a row with its uses, its
# wins and its losses, counted in turns. Every turn the engine records which memories went into the prompt. On the
# next message the user sends, that message is read as the outcome of the turn those memories sat in:
#
#   correction     ("that's wrong", "you're wrong", a leading "no")   a loss of COST_CORRECTION
#   appreciation   ("thanks", "you remembered", "exactly")            a win of GAIN_APPRECIATION
#   continued      anything else: the conversation carried on         a small win of GAIN_CONTINUED
#
# Effectiveness is the smoothed win share, so a memory starts at PRIOR and needs evidence to move. Retention then
# multiplies effectiveness by a staleness factor counted in turns since the memory was last retrieved. The staler a
# memory gets, the more it costs, and a memory with low effectiveness goes stale faster, because its half-life is
# shorter. That is the whole policy: low effectiveness decays faster, and no wall clock is consulted anywhere.
#
# A memory is forgotten when its retention falls under FLOOR after at least MIN_USES retrievals, or when it was never
# retrieved within NEVER_USED_TURNS turns of being first seen. Forgetting an automatic entry rewrites palace.json
# without it; forgetting an episode drops it from episodes.json. A memory that is merely weak is still recallable:
# its rank multiplier only makes it lose ties.

import hashlib
import json
import math
import os
import re

FILE = "memory_policy.json"

PRIOR = 0.5               # effectiveness of a memory with no evidence yet
STRENGTH = 2.0            # pseudo-counts behind the prior: how much evidence the prior is worth
GAIN_APPRECIATION = 1.0   # a win for an appreciated turn
GAIN_CONTINUED = 0.15     # a small win for a turn the user simply carried on
COST_CORRECTION = 1.5     # a loss for a turn the user corrected

MIN_USES = 2              # retrievals needed before low retention can forget a memory
FLOOR = 0.12              # retention under this, after MIN_USES, is forgotten
NEVER_USED_TURNS = 24     # turns after first sight before an unretrieved memory is forgotten
BASE_HALF = 8.0           # turns of a staleness half-life at zero effectiveness
SPAN_HALF = 24.0          # added turns of half-life at full effectiveness
FORGET_EVERY = 6          # turns between forgetting passes

# The phrases that read as a correction or as appreciation. English only, like the attitude keywords.
CORRECTION = re.compile(
    r"(?:\bthat'?s (?:not right|wrong|incorrect)\b|\byou'?re (?:wrong|mistaken)\b|\bnot true\b|\bincorrect\b"
    r"|\bi (?:didn'?t|did not|never) say\b|\byou misunderstood\b|\bnot quite\b|\bno wait\b"
    r"|\bthat'?s not what i\b|\bwrong again\b)", re.I)
# A leading "no" reads as a correction, unless it opens one of the harmless phrases ("no worries", "no problem",
# "no thanks"). Those say something about the user's mood, not about what the character just wrote.
LEADING_NO = re.compile(
    r"^\s*(?:no|nope|nah)\b(?!\s+(?:worries|worry|problem|thanks|thank you|need|idea|way|chance|doubt|rush|pressure))"
    r"[\s,.!-]", re.I)
APPRECIATION = re.compile(
    r"(?:\bthank(?:s| you)\b|\bthx\b|\bty\b|\bappreciate\b|\byou remembered\b|\bgood memory\b"
    r"|\bwell remembered\b|\bexactly\b|\bperfect\b|\bthat'?s right\b|\bthats right\b|\bgood job\b"
    r"|\bwell done\b|\bnice one\b|\bspot on\b|\bgood point\b)", re.I)


# A fresh state for a chat that has never been scored.
def fresh():
    return {"turn": 0, "items": {}, "pending": {"turn": 0, "keys": []}}


# The state from disk, or a fresh one. A damaged file means no policy, never a broken chat.
def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return fresh()
    if not isinstance(data, dict) or not isinstance(data.get("items"), dict):
        return fresh()
    state = fresh()
    state["turn"] = max(0, int(data.get("turn", 0) or 0))
    for key, row in data["items"].items():
        if not isinstance(row, dict):
            continue
        state["items"][str(key)] = {"uses": float(row.get("uses", 0) or 0),
                                    "wins": float(row.get("wins", 0) or 0),
                                    "losses": float(row.get("losses", 0) or 0),
                                    "first": int(row.get("first", 0) or 0),
                                    "last": int(row.get("last", 0) or 0),
                                    "kind": str(row.get("kind") or ""),
                                    "title": str(row.get("title") or "")}
    pending = data.get("pending") if isinstance(data.get("pending"), dict) else {}
    keys = pending.get("keys") if isinstance(pending.get("keys"), list) else []
    state["pending"] = {"turn": int(pending.get("turn", 0) or 0), "keys": [str(k) for k in keys if k]}
    return state


def save(path, state):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, path)


# A stable key for a memory that survives edits to other memories: an automatic entry uses its id, a legacy one
# hashes its content, and an episode hashes its text.
def palace_key(entry):
    entry_id = str(entry.get("id") or "").strip()
    if entry_id:
        return "p:" + entry_id
    return "p:" + hashlib.sha1(str(entry.get("content") or "").encode("utf-8")).hexdigest()[:10]


# The smoothed win share: the prior until evidence arrives, then the observed share.
def effectiveness(row):
    if not row:
        return PRIOR
    wins = float(row.get("wins", 0) or 0)
    losses = float(row.get("losses", 0) or 0)
    return (wins + PRIOR * STRENGTH) / (wins + losses + STRENGTH)


# How many turns of staleness one halving of a memory's standing takes. A weak memory halves four times sooner than
# a strong one, which is what makes low effectiveness decay faster.
def half_life(row):
    return BASE_HALF + SPAN_HALF * effectiveness(row)


# The standing of a memory now: effectiveness discounted by the turns since it was last retrieved. A memory never
# retrieved yet ages from the turn it was first seen, so it is not credited with a standing it never earned.
def retention(state, row, turn=None):
    if not row:
        return PRIOR
    now = state.get("turn", 0) if turn is None else turn
    anchor = int(row.get("last", 0) or 0) or int(row.get("first", 0) or 0)
    stale = max(0, now - anchor)
    return effectiveness(row) * (0.5 ** (stale / half_life(row)))


# What a retrieval rank is multiplied by. The multiplier only ever demotes: a memory at full effectiveness keeps its
# own order, a weak one is pushed down. palace.rank clamps it to at most 1.0, so a memory the character wrote can
# never be promoted above an entry the user wrote with the same order.
def rank_multiplier(state, key):
    return 0.35 + 0.65 * effectiveness(state["items"].get(key))


# The outcome a user message reads as, with the credit it carries. A correction beats appreciation beats a plain
# continuation, so one message moves a memory one way only.
def outcome(text):
    text = str(text or "")
    if not text.strip():
        return "none", 0.0, 0.0
    if CORRECTION.search(text) or LEADING_NO.search(text):
        return "correction", 0.0, COST_CORRECTION
    if APPRECIATION.search(text):
        return "appreciation", GAIN_APPRECIATION, 0.0
    return "continued", GAIN_CONTINUED, 0.0


# Starts a turn: the counter the retention works in. Called once per user turn, before anything is recorded.
def start_turn(state):
    state["turn"] = int(state.get("turn", 0) or 0) + 1
    return state["turn"]


# Records that these memories went into the prompt this turn: items is a list of (key, kind, title). A key seen for
# the first time starts its row, so an unretrieved memory can later be forgotten for never having been used.
def record(state, items):
    now = state["turn"]
    rows = state["items"]
    keys = []
    for key, kind, title in items:
        row = rows.get(key)
        if row is None:
            row = {"uses": 0.0, "wins": 0.0, "losses": 0.0, "first": now, "last": 0, "kind": "", "title": ""}
            rows[key] = row
        row["uses"] = float(row.get("uses", 0) or 0) + 1
        row["last"] = now
        if kind:
            row["kind"] = kind
        if title:
            row["title"] = str(title)[:120]
        keys.append(key)
    state["pending"] = {"turn": now, "keys": keys}


# Notes that a memory exists even when it was not retrieved, so an entry that never matches is still on the books.
def seen(state, key, kind="", title=""):
    row = state["items"].get(key)
    if row is None:
        state["items"][key] = {"uses": 0.0, "wins": 0.0, "losses": 0.0, "first": state["turn"], "last": 0,
                               "kind": kind, "title": str(title or "")[:120]}
    elif title and not row.get("title"):
        row["title"] = str(title)[:120]


# Credits the memories of the last turn from the message the user has just sent. Returns the outcome's name and how
# many memories were credited. A pending set older than a turn still counts: the user came back to that turn.
def settle(state, text):
    pending = state.get("pending") or {}
    keys = [k for k in pending.get("keys", []) if k]
    if not keys:
        return "none", 0
    name, win, loss = outcome(text)
    credited = 0
    for key in keys:
        row = state["items"].get(key)
        if row is None:
            continue
        row["wins"] = float(row.get("wins", 0) or 0) + win
        row["losses"] = float(row.get("losses", 0) or 0) + loss
        credited += 1
    state["pending"] = {"turn": state["turn"], "keys": []}
    return name, credited


# Whether a memory has earned removal: never used and old, or used and now standing too low.
def should_forget(state, key, turn=None):
    row = state["items"].get(key)
    if not row:
        return False
    now = state.get("turn", 0) if turn is None else turn
    uses = float(row.get("uses", 0) or 0)
    if uses <= 0:
        return now - int(row.get("first", 0) or 0) >= NEVER_USED_TURNS
    return uses >= MIN_USES and retention(state, row, now) < FLOOR


# Drops the rows of memories no longer stored, so the file does not grow with every episode ever cut.
def prune(state, live_keys):
    live = set(live_keys)
    state["items"] = {k: v for k, v in state["items"].items() if k in live}


# The figures the settings page shows: one line per memory, strongest first.
def report(state, live):
    # live maps a key to (kind, title) for the memories that still exist.
    out = []
    for key, row in state["items"].items():
        meta = live.get(key)
        if meta is None:
            continue
        out.append({"kind": meta[0], "title": meta[1] or row.get("title") or "Untitled",
                    "effectiveness": round(effectiveness(row), 3),
                    "retention": round(retention(state, row), 3),
                    "uses": int(row.get("uses", 0) or 0),
                    "wins": round(float(row.get("wins", 0) or 0), 2),
                    "losses": round(float(row.get("losses", 0) or 0), 2),
                    "forgets_in": turns_left(state, key)})
    out.sort(key=lambda item: (-item["retention"], item["title"].lower()))
    return out


# Roughly how many turns the memory has before it would be forgotten, for the settings page. Retention falls under
# FLOOR when effectiveness * 0.5 ** (stale / half_life) < FLOOR, which is stale = half_life * log2(effectiveness/FLOOR).
def turns_left(state, key):
    row = state["items"].get(key)
    if not row:
        return None
    uses = float(row.get("uses", 0) or 0)
    if uses <= 0:
        return max(0, NEVER_USED_TURNS - (state["turn"] - int(row.get("first", 0) or 0)))
    if uses < MIN_USES:
        return None
    need = half_life(row) * (math.log(max(effectiveness(row), 1e-6) / FLOOR) / math.log(2))
    return max(0, int(need) - (state["turn"] - int(row.get("last", 0) or 0)))
