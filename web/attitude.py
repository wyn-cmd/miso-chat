# Attitude: how a character regards the user. Two scores from 0 to 100, affection and trust, move with the events a
# message triggers and with the number of exchanges. The clock plays no part. Five relationship levels are read from
# the scores, from stranger to married. Events are found by keyword, so no model call is needed. Solo characters
# only.
#
# Each event may count once every COOLDOWN exchanges, so repeating a word does not raise the scores. Bonding events
# are the things a user does to keep the relationship going: gifts, doing something together, and asking about the
# character. If no bonding event comes for a long run of exchanges, trust eases down a little, one point every
# DECAY_EVERY exchanges past DECAY_AFTER. Marriage is never reached by the scores alone; it takes a proposal once the
# bond is partner-level and high enough.

import json
import re

LEVELS = ("stranger", "acquaintance", "friend", "partner", "married")
START_AFFECTION = 5.0
START_TRUST = 5.0

COOLDOWN = 9              # exchanges before the same event may count again
MAX_TURN = 6.0            # most the scores may move in one exchange, up or down
HOLD_MARGIN = 5.0         # a level is kept until a score falls this far below its mark
DECAY_AFTER = 5           # exchanges without a bonding event before trust starts to ease
DECAY_EVERY = 5           # then one more point of trust eases every this many exchanges
DECAY_TRUST = 1.0
MARRY_AFFECTION = 85.0    # the proposal is accepted only from partner level, at these scores
MARRY_TRUST = 75.0

# What each level needs: affection and trust at or above these marks. Married is not in here; see level_for.
MARKS = {
    "acquaintance": (15.0, 0.0),
    "friend": (35.0, 30.0),
    "partner": (65.0, 55.0),
}

# Events: keywords found in the user's message, the change they make, and whether they count as bonding.
EVENTS = {
    "gift": {"keys": [r"\bgift\b", r"\bbrought you\b", r"\bpresent for you\b"],
             "affection": 4, "trust": 1, "bonding": True,
             "says": "the user gave you a gift"},
    "together": {"keys": [r"\blet'?s (go|do|play|watch|cook|eat|walk|talk)\b", r"\btogether\b", r"\bgo out\b",
                          r"\bplay a game\b", r"\bspend (some )?time\b"],
                 "affection": 2, "trust": 1, "bonding": True,
                 "says": "the user suggested doing something together"},
    "cuddle": {"keys": [r"\bcuddl(e|es|ed|ing)\b", r"\bsnuggl(e|es|ed|ing)\b"],
               "affection": 2, "trust": 1, "bonding": True,
               "says": "the user cuddled you"},
    "rest": {"keys": [r"\b(get some rest|have a rest|take a rest|let'?s rest|we can rest|i (want|need) to rest)\b",
                      r"\b(i )?(need|want) (some )?rest\b", r"\brest (with|beside|next to) you\b", r"\bresting\b",
                      r"\b(let'?s|we can|can we|i (want|need) to) (sleep|nap)\b",
                      r"\bsleep (with|beside|next to|together)\b", r"\b(go to|going to|time to) sleep\b",
                      r"\bsleeping\b", r"\bnap(ping|s|ped)?\b"],
             "affection": 1, "trust": 1, "bonding": True,
             "says": "the user rested with you"},
    "share": {"keys": [r"\bhow was your day\b", r"\btell me about yourself\b", r"\bwhat do you like\b",
                       r"\bwhat'?s your favou?rite\b"],
              "affection": 2, "trust": 1, "bonding": True,
              "says": "the user asked about you"},
    "compliment": {"keys": [r"\b(beautiful|lovely|amazing|wonderful|adorable|cute)\b", r"\bproud of you\b"],
                   "affection": 2, "trust": 0, "bonding": False,
                   "says": "the user paid you a compliment"},
    "support": {"keys": [r"\bi'?m here for you\b", r"\bare you (ok|okay|alright)\b", r"\blet me help\b",
                         r"\bi'?ll help you\b", r"\bi believe in you\b"],
                "affection": 1, "trust": 3, "bonding": False,
                "says": "the user offered you support"},
    "apology": {"keys": [r"\bi'?m sorry\b", r"\bmy mistake\b", r"\bforgive me\b", r"\bi apologi[sz]e\b"],
                "affection": 1, "trust": 2, "bonding": False,
                "says": "the user apologised"},
    "insult": {"keys": [r"\bshut up\b", r"\bidiot\b", r"\bstupid\b", r"\bi hate you\b", r"\buseless\b",
                        r"\bworthless\b"],
               "affection": -4, "trust": -3, "bonding": False,
               "says": "the user was unkind to you"},
    "dismissive": {"keys": [r"\bwhatever\b", r"\bboring\b", r"\bleave me alone\b"],
                   "affection": -2, "trust": 0, "bonding": False,
                   "says": "the user brushed you off"},
    "deceit": {"keys": [r"\bi lied\b", r"\bi was lying\b", r"\bi (didn'?t|did not) tell you\b"],
               "affection": -1, "trust": -4, "bonding": False,
               "says": "the user admitted to deceiving you"},
}
PROPOSAL = {"keys": [r"\bmarry me\b", r"\bwill you marry\b"], "says": "the user asked you to marry them"}

COMPILED = {name: [re.compile(k, re.I) for k in ev["keys"]] for name, ev in EVENTS.items()}
COMPILED_PROPOSAL = [re.compile(k, re.I) for k in PROPOSAL["keys"]]

# The stored columns, in the order the store keeps them.
KEYS = ("affection", "trust", "level", "exchanges", "since_bond", "last_fired")

TONE = {
    "stranger": "You do not know the user well. Be polite and a little guarded.",
    "acquaintance": "You know the user a little. Be friendly, but keep some distance.",
    "friend": "You trust the user and enjoy their company. Be warm and open.",
    "partner": "You are close to the user and affectionate. Let that show.",
    "married": "You are married to the user. Speak as a spouse does, with ease and devotion.",
}


# A fresh state for a pair that has never talked.
def fresh():
    return {"affection": START_AFFECTION, "trust": START_TRUST, "level": "stranger", "exchanges": 0,
            "since_bond": 0, "last_fired": {}}


# The state from a stored row, or a fresh one when there is no row.
def load(row):
    if not row:
        return fresh()
    fired = row["last_fired"]
    if isinstance(fired, str):
        try:
            fired = json.loads(fired or "{}")
        except ValueError:
            fired = {}
    return {"affection": float(row["affection"]), "trust": float(row["trust"]), "level": str(row["level"]),
            "exchanges": int(row["exchanges"]), "since_bond": int(row["since_bond"]),
            "last_fired": {str(k): int(v) for k, v in dict(fired).items()}}


# The row to store: the same fields, with the cooldown record as text.
def to_row(state):
    return {"affection": state["affection"], "trust": state["trust"], "level": state["level"],
            "exchanges": state["exchanges"], "since_bond": state["since_bond"],
            "last_fired": json.dumps(state["last_fired"], sort_keys=True)}


# The event names whose keywords appear in the message, in the table's order. The proposal is listed last.
def detect(text):
    found = [name for name in EVENTS if any(p.search(text or "") for p in COMPILED[name])]
    if any(p.search(text or "") for p in COMPILED_PROPOSAL):
        found.append("proposal")
    return found


def _clamp(value, low, high):
    return max(low, min(high, value))


# The level the scores support. Each level holds until its score falls HOLD_MARGIN below the mark. Married is only
# kept, never earned, here: it comes from the proposal in apply.
def level_for(current, affection, trust):
    best = "stranger"
    current_rank = LEVELS.index(current) if current in LEVELS else 0
    for level in LEVELS[1:]:
        if level == "married":
            if current != "married":
                break
            need_affection, need_trust = MARRY_AFFECTION, MARRY_TRUST
        else:
            need_affection, need_trust = MARKS[level]
        margin = HOLD_MARGIN if current_rank >= LEVELS.index(level) else 0.0
        if affection >= need_affection - margin and trust >= need_trust - margin:
            best = level
        else:
            break
    return best


# One exchange: the message is read for events, the scores move, trust may ease, and the level is re-read.
# A character's relationship speed multiplies the positive change only; unkindness and trust decay are not
# scaled. Returns the new state and what happened this turn, for the prompt.
def _move(values, multiplier):
    positive = min(sum(v for v in values if v > 0), MAX_TURN) * multiplier
    negative = max(sum(v for v in values if v < 0), -MAX_TURN)
    return positive + negative


def apply(state, text, affection_multiplier=1.0, trust_multiplier=1.0):
    s = dict(state)
    s["last_fired"] = dict(state["last_fired"])
    s["exchanges"] += 1
    n = s["exchanges"]
    found = detect(text)
    took = []   # events whose change counts this exchange, after the cooldown
    for name in found:
        if name == "proposal":
            continue
        if n - s["last_fired"].get(name, -COOLDOWN) < COOLDOWN:
            continue
        s["last_fired"][name] = n
        took.append(name)
    affection = _move([EVENTS[e]["affection"] for e in took], max(0.0, affection_multiplier))
    trust = _move([EVENTS[e]["trust"] for e in took], max(0.0, trust_multiplier))
    s["affection"] = _clamp(s["affection"] + affection, 0.0, 100.0)
    s["trust"] = _clamp(s["trust"] + trust, 0.0, 100.0)

    # Trying to bond counts as bonding even when the cooldown stops the change.
    bonding = any(EVENTS[e]["bonding"] for e in found if e != "proposal")
    decayed = False
    if bonding:
        s["since_bond"] = 0
    else:
        s["since_bond"] += 1
        if s["since_bond"] >= DECAY_AFTER and (s["since_bond"] - DECAY_AFTER) % DECAY_EVERY == 0:
            s["trust"] = max(0.0, s["trust"] - DECAY_TRUST)
            decayed = True

    proposed = False
    if ("proposal" in found and s["level"] == "partner"
            and s["affection"] >= MARRY_AFFECTION and s["trust"] >= MARRY_TRUST):
        s["level"] = "married"
        proposed = True
    else:
        s["level"] = level_for(s["level"], s["affection"], s["trust"])
    return s, {"took": took, "proposal": "proposal" in found, "proposed": proposed, "decayed": decayed}


# The block the engine puts in the prompt for this turn. The numbers are for the character's own tone.
def describe(state, result):
    lines = ["--- Your relationship with the user (kept by the simulation; act on it, do not discuss the numbers) ---",
             f"Level: {state['level']}. Affection {round(state['affection'])} of 100. Trust {round(state['trust'])} of 100."]
    said = [EVENTS[e]["says"] for e in result["took"]]
    if result["proposal"] and state["level"] != "married":
        if result["proposed"]:
            said.append("the user asked you to marry them, and you said yes")
        else:
            said.append("the user asked you to marry them, and the bond is not ready for it; say so kindly")
    if said:
        lines.append("Just now: " + "; ".join(said) + ".")
    if result["decayed"]:
        lines.append("You have had little closeness lately, so your trust is a little lower.")
    lines.append(TONE[state["level"]])
    if state["trust"] < 25 and state["level"] != "stranger":
        lines.append("You are not sure you can rely on the user yet; hold back a little.")
    return "\n".join(lines)
