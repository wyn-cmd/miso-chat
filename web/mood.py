# Character state: emotion, energy and affinity, kept as numbers and moved by events and by elapsed time. The engine
# reads the state into the prompt, so the model is not asked to infer the mood from scratch each turn. Solo
# characters only. Nothing here calls a model.
#
# Energy (0 to 100) recovers slowly in silence and falls only once a stretch of talk passes the lite allowance: the
# number of messages a lite account could send in a day. Within that allowance a character is never worn out, so
# free and lite users do not meet exhaustion. Affinity (0 to 100) rises a little with each exchange and fades after
# several days of silence, down to a floor. Emotion is a small state machine over calm, warm, tired and lonely. Its
# moves depend on energy, affinity and hours of silence, with a margin on the way back so a value near a line does
# not flip every turn. A lonely character reaches out once per silence, through the check-in path.

import re

EMOTIONS = ("calm", "warm", "tired", "lonely")
ENERGY_MAX = 100.0
AFFINITY_MAX = 100.0
START_ENERGY = 80.0
START_AFFINITY = 30.0

EXHAUST_COST = 3.0         # energy one exchange costs once a stretch of talk is past the lite allowance
TOKENS_PER_EXCHANGE = 7000  # estimate: a 25-message window at about 150 tokens each, a character card and palace
                            # block of about 2,000, and a reply of about 300, with headroom
REST_AFTER_HOURS = 2       # a silence this long is a rest, and the conversation count starts again
REST_PER_HOUR = 6.0        # energy regained per hour of silence
EXCHANGE_GAIN = 0.9 / 3    # affinity gained per exchange
MOOD_LINK_RATE = 0.08      # each reply, mood affinity closes this share of its gap to the relationship's affection
FADE_AFTER_HOURS = 168    # silence before affinity starts to fade
FADE_PER_DAY = 0.5         # affinity lost per day of silence beyond that
AFFINITY_FLOOR = 10.0
TIRED_BELOW = 25.0         # energy under this makes a character tired
TIRED_ENDS = 40.0          # and it stays tired until energy reaches this
LONELY_AFTER_HOURS = 48    # this long in silence, with affinity high enough, makes a character lonely
LONELY_MIN_AFFINITY = 35.0
WARM_AT = 70.0             # affinity at this makes a character warm
WARM_ENDS = 60.0           # and it stays warm until affinity falls below this
COMFORT_GAIN = 2.0         # energy regained by a cuddle, a rest or a sleep in the chat

# Rest and closeness in the chat itself: a small energy lift, enough to show without undoing the longer rest.
COMFORT_KEYS = [
    re.compile(r"\bcuddl(e|es|ed|ing)\b", re.I),
    re.compile(r"\bsnuggl(e|es|ed|ing)\b", re.I),
    re.compile(r"\b(get some rest|have a rest|take a rest|let'?s rest|we can rest|i (want|need) to rest)\b", re.I),
    re.compile(r"\b(i )?(need|want) (some )?rest\b", re.I),
    re.compile(r"\brest (with|beside|next to) you\b", re.I),
    re.compile(r"\bresting\b", re.I),
    re.compile(r"\b(let'?s|we can|can we|i (want|need) to) (sleep|nap)\b", re.I),
    re.compile(r"\bsleep (with|beside|next to|together)\b", re.I),
    re.compile(r"\b(go to|going to|time to) sleep\b", re.I),
    re.compile(r"\bsleeping\b", re.I),
    re.compile(r"\bnap(ping|s|ped)?\b", re.I),
]


def comforting(text):
    return any(pattern.search(text or "") for pattern in COMFORT_KEYS)


# What a message does to affinity beyond the flat gain each exchange. Each event is a gain (or a loss, when negative)
# for a message that matches one of its phrases. The gains from the user's message and half of the character's reply
# are added together and capped per exchange, so no amount of warm wording pushes affinity far in one turn.
AFFINITY_EVENTS = {
    "greeting": (0.8, [r"\bgood (morning|night|evening)\b", r"\bsweet dreams\b", r"\btake care\b",
                       r"\bhope you('re| are) (well|ok|okay|doing well|sleeping)\b", r"\bhow'?s your day\b"]),
    "humour": (0.8, [r"\bhaha+\b", r"\bhehe+\b", r"\blol\b", r"\blmao\b", r"\bmade me laugh\b", r"\bthat'?s funny\b",
                     r"\byou'?re funny\b", r"\bso funny\b"]),
    "missed": (1.5, [r"\bi (really )?missed you\b", r"\bmiss you\b", r"\bglad to see you\b", r"\bgood to see you\b",
                     r"\bgood to hear from you\b", r"\bi'?m back\b", r"\bit'?s me again\b"]),
    "delight": (0.6, [r"\bi'?m (so )?happy\b", r"\blove this\b", r"\bthat'?s (great|wonderful|lovely)\b",
                      r"\blovely day\b", r"\bwhat a (great|lovely|wonderful)\b"]),
    "kind": (0.6, [r"\bthank(s| you)\b", r"\bappreciate (you|it|that)\b", r"\bgrateful\b"]),
    "cared": (0.8, [r"\bhow have you been\b", r"\bhow are you (doing|feeling)\b", r"\bare you (alright|ok|okay)\b",
                    r"\bi was thinking (about|of) you\b", r"\bthinking of you\b"]),
    "dismissed": (-1.0, [r"\bgo away\b", r"\bleave me alone\b", r"\bshut up\b", r"\bi'?m bored\b", r"\byou'?re boring\b"]),
    "unkind": (-1.0, [r"\bidiot\b", r"\bstupid\b", r"\buseless\b", r"\bworthless\b", r"\bi hate you\b"]),
}
AFFINITY_EVENTS_COMPILED = {name: (gain, [re.compile(p, re.I) for p in pats])
                            for name, (gain, pats) in AFFINITY_EVENTS.items()}
# Most a message may add through events in one exchange (the user's words, and half the reply's).
AFFINITY_EVENT_CAP = 3.0
REPLY_SCALE = 0.5
# A user who comes back after a long silence gets this much, on top of the flat gain, whatever they say.
WELCOME_BACK = 0.5
# Words that cancel an event when they sit just before it, as in the attitude keywords.
_NEGATIONS = {"not", "never", "nothing", "nobody", "without", "dont", "didnt", "doesnt", "wont", "cant", "cannot",
              "isnt", "arent", "wasnt", "werent"}


def _event_hits(text):
    # Each event at most once per message, with no negation in the words just before its phrase.
    hits = []
    for name, (gain, patterns) in AFFINITY_EVENTS_COMPILED.items():
        for p in patterns:
            found = False
            for m in p.finditer(text):
                clause = re.split(r"[.!?;\n]", text[:m.start()])[-1]
                before = [w.replace("'", "").lower() for w in re.findall(r"[\w']+", clause)][-3:]
                if not _NEGATIONS.intersection(before):
                    found = True
                    break
            if found:
                hits.append(gain)
                break
    return hits


def affinity_from_text(text, reply=""):
    # The event gains for one exchange: the user's words, plus half of the character's reply, the positives capped.
    user = _event_hits(text or "")
    char = _event_hits(reply or "")
    positive = sum(g for g in user if g > 0) + REPLY_SCALE * sum(g for g in char if g > 0)
    negative = sum(g for g in user if g < 0) + REPLY_SCALE * sum(g for g in char if g < 0)
    return min(AFFINITY_EVENT_CAP, positive) + negative

TONE = {
    "calm": "You are in an ordinary mood.",
    "warm": "You are fond of the user, and it shows in how readily you engage.",
    "tired": "You are worn out after a long stretch of talk. Let it show: shorter sentences, fewer ideas, less energy.",
    "lonely": "You have missed the user. Be glad to hear from them and say so, without making them feel guilty.",
}


# A fresh state for a pair that has never chatted. Silence starts now, so it is not lonely at once.
def fresh(now):
    return {"energy": START_ENERGY, "affinity": START_AFFINITY, "emotion": "calm", "streak": 0,
            "last_update": now, "last_user": now, "reached": 0.0}


# The state from a stored row, or a fresh one when there is no row.
def load(row, now):
    if not row:
        return fresh(now)
    return {"energy": float(row["energy"]), "affinity": float(row["affinity"]), "emotion": str(row["emotion"]),
            "streak": int(row["streak"]), "last_update": float(row["last_update"]),
            "last_user": float(row["last_user"]), "reached": float(row["reached"])}


# The emotion state machine. Tired first, then lonely, then warm, calm otherwise. Each threshold has a margin on the
# way back, so an emotion holds until the value has clearly moved.
def next_emotion(current, energy, affinity, silent_hours):
    if energy < TIRED_BELOW or (current == "tired" and energy < TIRED_ENDS):
        return "tired"
    if silent_hours >= LONELY_AFTER_HOURS and affinity >= LONELY_MIN_AFFINITY:
        return "lonely"
    if affinity >= WARM_AT or (current == "warm" and affinity >= WARM_ENDS):
        return "warm"
    return "calm"


# Moves a state from its last update to now: silence restores energy and may fade affinity, then the emotion is
# re-checked. Returns a new dict and leaves the old one alone.
def advance(state, now):
    s = dict(state)
    silent_before = max(0.0, s["last_update"] - s["last_user"]) / 3600
    silent_now = max(0.0, now - s["last_user"]) / 3600
    # Energy comes back only in silence past a rest, never while the user is chatting.
    rest_hours = max(0.0, silent_now - REST_AFTER_HOURS) - max(0.0, silent_before - REST_AFTER_HOURS)
    s["energy"] = min(ENERGY_MAX, s["energy"] + REST_PER_HOUR * rest_hours)
    # Only the part of this stretch that lies beyond the fade threshold counts toward the fade.
    fade_hours = max(0.0, silent_now - FADE_AFTER_HOURS) - max(0.0, silent_before - FADE_AFTER_HOURS)
    s["affinity"] = max(AFFINITY_FLOOR, s["affinity"] - FADE_PER_DAY * fade_hours / 24)
    s["last_update"] = now
    s["emotion"] = next_emotion(s["emotion"], s["energy"], s["affinity"], silent_now)
    return s


# How many messages a lite account may send in a day: the lower of its daily token share and its daily request cap.
# Dividing by the estimated tokens per exchange gives the messages the tokens allow.
def lite_allowance(daily_tokens, request_cap):
    return max(1, min(int(request_cap), int(daily_tokens) // TOKENS_PER_EXCHANGE))


# One exchange: the user wrote and the character answered. The state is first moved to now. A silence long enough
# to count as a rest starts the conversation count again. Only exchanges past the allowance cost energy. Every
# exchange adds affinity and resets silence.
def on_exchange(state, now, allowance, text="", reply=""):
    s = advance(state, now)
    back_after_silence = (now - s["last_user"]) / 3600 >= REST_AFTER_HOURS
    if back_after_silence:
        s["streak"] = 0
    if s["streak"] >= allowance:
        s["energy"] = max(0.0, s["energy"] - EXHAUST_COST)
    gain = EXCHANGE_GAIN + affinity_from_text(text, reply) + (WELCOME_BACK if back_after_silence else 0.0)
    if comforting(text):
        gain += 0.5   # a cuddle or a rest in the chat is closeness as well as energy
        s["energy"] = min(ENERGY_MAX, s["energy"] + COMFORT_GAIN)
    s["affinity"] = max(AFFINITY_FLOOR, min(AFFINITY_MAX, s["affinity"] + gain))
    s["streak"] += 1
    s["last_user"] = now
    s["emotion"] = next_emotion(s["emotion"], s["energy"], s["affinity"], 0.0)
    return s


# A lonely character reaches out once per silence: not again until the user writes.
# Moves mood affinity a little toward the relationship's affection. Only mood follows the relationship, never the other way.
def link_to(state, affection):
    s = dict(state)
    target = _clamp_value(float(affection), AFFINITY_FLOOR, AFFINITY_MAX)
    s["affinity"] = _clamp_value(s["affinity"] + (target - s["affinity"]) * MOOD_LINK_RATE, AFFINITY_FLOOR, AFFINITY_MAX)
    return s


def _clamp_value(value, low, high):
    return max(low, min(high, value))


def outreach_due(state, now):
    return state["emotion"] == "lonely" and state["reached"] < state["last_user"]


# Records that a check-in went out for this silence.
def on_outreach(state, now):
    s = dict(state)
    s["reached"] = now
    return s


# The band a value falls in, for the prompt and the settings page.
def energy_band(value):
    if value < 35:
        return "low"
    if value < 70:
        return "medium"
    return "high"


def affinity_band(value):
    if value < 25:
        return "distant"
    if value < 50:
        return "friendly"
    if value < 75:
        return "close"
    return "devoted"


# The block the engine puts in the prompt. The numbers are for the character's own tone, not for the user to see.
def describe_stable(state):
    # The part of the mood that changes only with the emotion or a band (energy low/medium/high, affinity distant to
    # devoted). The numbers change every turn, so they are not sent. This text sits in the system prompt, which is
    # rebuilt only when it changes.
    energy, affinity = round(state["energy"]), round(state["affinity"])
    return "\n".join([
        "[How you feel right now]",
        f"Emotion: {state['emotion']}. Energy {energy_band(energy)}. Affinity {affinity_band(affinity)}.",
        TONE[state["emotion"]],
    ])
