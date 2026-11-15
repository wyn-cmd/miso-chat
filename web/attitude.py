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
    "gift": {"keys": [r"\bgift\b", r"\bbrought you\b", r"\bpresent for you\b", r"\bbought you\b", r"\bgot you (a|an|some)\b",
                      r"\bsomething for you\b", r"\bmade you (a|an|some)\b", r"\bflowers for you\b"],
             "affection": 4.5, "trust": 1, "bonding": True,
             "says": "the user gave you a gift"},
    "together": {"keys": [r"\blet'?s (go|do|play|watch|cook|eat|walk|talk|sit|stay|hang|read|sing|dance|explore|relax)\b",
                          r"\btogether\b", r"\bgo out\b", r"\bgo on a date\b", r"\bhang out\b", r"\bkeep me company\b",
                          r"\bplay a game\b", r"\bspend (some )?time\b", r"\bshare (a|some) (meal|drink|tea|coffee|snack)\b"],
                 "affection": 2.5, "trust": 1, "bonding": True,
                 "says": "the user suggested doing something together"},
    "cuddle": {"keys": [r"\bcuddl(e|es|ed|ing)\b", r"\bsnuggl(e|es|ed|ing)\b", r"\bhug(s|ged|ging)?\b",
                        r"\bhold(ing)? (you|your hand)\b", r"\bheld (you|your hand)\b", r"\bkiss(es|ed|ing)?\b"],
               "affection": 2.5, "trust": 1, "bonding": True,
               "says": "the user cuddled you"},
    "lean": {"keys": [r"\blean(s|ed|ing)? (on|against|into|in|close to|closer to|toward|towards) (you|your)\b",
                      r"\bleaning (close|closer|in)\b", r"\bnestl(e|ed|es|ing) (in|against|into) you\b",
                      r"\bnestl(e|ed|es|ing)\b(?:\W+\w+){0,2}?\W+(you|your|side)\b",
                      r"\bpress(ed|es|ing)? (close|against) you\b", r"\bshoulder to shoulder\b",
                      r"\bsit(ting)? (close|closer|beside|next to) you\b",
                      # A short gap of up to two words, so "leaned against his shoulder" or "lean on her arm" match.
                      r"\blean(s|ed|ing)?\b(?:\W+\w+){0,2}?\W+(you|your|shoulders?|chest|arms?)\b",
                      r"\bhead (on|against|to) (your|his|her|their|my) (shoulder|chest)\b",
                      r"\b(rest|rested|rests|resting|nestle|nestled|nestles|nestling)\b(?:\W+\w+){0,2}?\W+shoulder\b"],
             "affection": 2.5, "trust": 1, "bonding": True,
             "says": "the user leaned in close to you"},
    "confide": {"keys": [r"\bconfid(e|ed|es|ing) in you\b", r"\bi (really |truly |do )?trust you\b",
                         r"\brel(y|ied|ies|ying) (heavily )?on you\b", r"\bcount on you\b", r"\bdepend on you\b",
                         r"\bopen up to you\b", r"\bi can tell you anything\b", r"\btell you (a|my) (secret|secrets)\b",
                         r"\bhonest with you\b"],
                "affection": 1.5, "trust": 2.5, "bonding": True,
                "says": "the user confided in you"},
    "rest": {"keys": [r"\b(get some rest|have a rest|take a rest|let'?s rest|we can rest|i (want|need) to rest)\b",
                      r"\b(i )?(need|want) (some )?rest\b", r"\brest (with|beside|next to) you\b", r"\bresting\b",
                      r"\b(let'?s|we can|can we|i (want|need) to) (sleep|nap)\b",
                      r"\bsleep (with|beside|next to|together)\b", r"\b(go to|going to|time to) sleep\b",
                      r"\bsleeping\b", r"\bnap(ping|s|ped)?\b"],
             "affection": 1.5, "trust": 1, "bonding": True,
             "says": "the user rested with you"},
    "share": {"keys": [r"\bhow was your day\b", r"\btell me about yourself\b", r"\bwhat do you like\b",
                       r"\bwhat'?s your favou?rite\b", r"\btell me (more )?about your\b", r"\bhow have you been\b",
                       r"\bhow are you (doing|feeling|holding up)\b", r"\bwhat'?s on your mind\b",
                       r"\bwhat are your (dreams|hobbies|plans|thoughts|favou?rites)\b", r"\bwhat'?s your (day|week|story|past|childhood)\b",
                       r"\bdo you have (any )?(family|siblings|friends)\b",
                       r"\bi'?d (love|like) to (hear|learn) (about|more about) (you|your)\b", r"\bwhat (shaped|inspires|makes) you\b",
                       r"\bwhat brings you joy\b", r"\byour (upbringing|history|background)\b"],
              "affection": 2.5, "trust": 1, "bonding": True,
              "says": "the user asked about you"},
    "compliment": {"keys": [r"\b(beautiful|lovely|amazing|wonderful|adorable|cute|handsome|pretty|gorgeous|sweet|clever|smart|talented|brilliant|charming|brave|admirable|exquisite|radiant|magnificent|splendid|delightful|captivating|enchanting|endearing|elegant|eloquent|remarkable|extraordinary|exceptional|commendable|impressive)\b",
                            r"\bproud of you\b", r"\byou'?re (the best|so sweet|so kind|so good|so talented)\b", r"\bi love your\b",
                            r"\byou are (the best|so kind|so good)\b"],
                   "affection": 2.5, "trust": 0, "bonding": False,
                   "says": "the user paid you a compliment"},
    "support": {"keys": [r"\bi'?m here for you\b", r"\bare you (ok|okay|alright)\b", r"\blet me help\b",
                         r"\bi'?ll help you\b", r"\bi believe in you\b", r"\bi'?ve got your back\b", r"\byou'?re not alone\b",
                         r"\bi'?m (on your side|with you)\b", r"\btake care of yourself\b", r"\bdon'?t worry\b",
                         r"\bcan i help\b", r"\bneed (any )?help\b",
                         r"\b(i'?ll )?stand (by|beside|with) you\b", r"\byou have my (support|backing)\b",
                         r"\b(always|forever) be there for you\b", r"\b(look after|watch over|protect|shelter) you\b",
                         r"\byou can (always )?lean on me\b", r"\bi'?m at your side\b"],
                "affection": 1.5, "trust": 3, "bonding": False,
                "says": "the user offered you support"},
    "apology": {"keys": [r"\bi'?m sorry\b", r"\bmy mistake\b", r"\bforgive me\b", r"\bi apologi[sz]e\b",
                         r"\bi was wrong\b", r"\bi shouldn'?t have\b", r"\bmy bad\b", r"\bi didn'?t mean\b", r"\bi regret\b",
                         r"\bplease forgive\b", r"\bi owe you an apology\b", r"\bbeg your (pardon|forgiveness)\b",
                         r"\bhumbly apologi[sz]e\b", r"\bdeeply regret\b"],
                "affection": 1.5, "trust": 2, "bonding": False,
                "says": "the user apologised"},
    "thanks": {"keys": [r"\bthank(s| you)\b", r"\bappreciate (you|it|that)\b", r"\bgrateful\b"],
               "affection": 1.5, "trust": 0, "bonding": False,
               "says": "the user thanked you"},
    "fondness": {"keys": [r"\b(i )?love you\b", r"\bmiss you\b", r"\bthinking (about|of) you\b", r"\bcare (about|for) you\b",
                          r"\badore you\b", r"\bmean (a lot|so much) to me\b", r"\bi (really )?like you\b",
                          r"\b(cherish|treasure|adore)s? you\b", r"\b(devoted|attached|fond) (to|of) you\b",
                          r"\byou (are|'re) (dear|precious|special) to me\b"],
                 "affection": 3, "trust": 0, "bonding": True,
                 "says": "the user told you how they feel about you"},
    "curious": {"keys": [r"\bwhy did you\b", r"\bwhy do you\b", r"\bhow did you (get|become|end up|learn|meet|start|decide)\b",
                         r"\bwhat made you\b", r"\bwhere did you (grow up|come from|learn|get|meet)\b",
                         r"\bwhat do you (think|feel) about\b", r"\bhow do you feel about\b", r"\bwhat'?s it like (to be|being)\b",
                         r"\bwho (taught|raised) you\b", r"\bdid you ever\b", r"\btell me (more|everything|the story|how)\b",
                         r"\bwhat (was|were) your\b", r"\bwho are you\b",
                         # "why do you do/say X": a question about the character's own reasons
                         r"\bwhy (do|did|would|does|were|was) you (say|do|think|feel|want|always|never|like|choose|keep|still|act)\b",
                         r"\bwhy are you (so|always|never|still|like|being)\b",
                         r"\bwhat do you mean\b", r"\bwhat did you mean\b", r"\bwhat are you (thinking|feeling|talking about)\b",
                         r"\bwhat happened (to you|with you)\b", r"\bwhat'?s your (story|deal|problem with)\b",
                         r"\bhow (do|did) you (know|manage|cope|handle|survive|learn to)\b",
                         r"\bwhat is it you (like|want|love|hate|fear)\b", r"\bwhat (scares|hurts|makes) you\b",
                         r"\bwhy (is|was) (that|this|it) (important|special|hard) (to|for) you\b"],
                 "affection": 2, "trust": 1.5, "bonding": True,
                 "says": "the user was curious about you and your past"},
    "promise": {"keys": [r"\bi promise\b", r"\bi swear\b", r"\byou have my word\b", r"\bi'?ll (always|never)\b",
                         r"\bi won'?t (break|leave|lie|betray|hurt|abandon|forget)\b", r"\bi'?m committed\b",
                         r"\bi commit to\b", r"\bcount on me\b", r"\bi'?ll keep my word\b", r"\bi give you my word\b"],
                "affection": 1, "trust": 2, "bonding": True,
                "says": "the user made a promise to you"},
    "reliability": {"keys": [r"\bas promised\b", r"\bi (did|kept|followed through|showed up|was there)\b",
                             r"\bi'?ll (be there|show up|follow through|keep (my word|that promise))\b",
                             r"\bi won'?t let you down\b", r"\bi'?ll make (good on|up for)\b", r"\bi came through\b"],
                   "affection": 0, "trust": 2, "bonding": True,
                   "says": "the user proved reliable to you"},
    "honesty": {"keys": [r"\bto be honest\b", r"\bi'?ll be honest\b", r"\bthe truth is\b", r"\bi'?m being (real|honest)\b",
                         r"\bno (more )?lies\b", r"\bi won'?t lie\b", r"\bhonestly\b", r"\btruthfully\b",
                         r"\bi'?m telling you the truth\b"],
                "affection": 0, "trust": 2, "bonding": True,
                "says": "the user was honest with you"},
    "safety": {"keys": [r"\byou'?re safe\b", r"\byou can tell me anything\b", r"\bi won'?t judge\b", r"\bi won'?t hurt you\b",
                        r"\bi'?ll protect you\b", r"\bi'?ll keep you safe\b", r"\byou can trust me\b",
                        r"\bi'?m (a )?safe (place|person)\b", r"\bwith me you'?re safe\b", r"\bi'?ll (look after|watch over) you\b"],
               "affection": 1, "trust": 3, "bonding": True,
               "says": "the user made you feel safe"},
    "respect": {"keys": [r"\bi respect you\b", r"\bi admire you\b", r"\bi value you\b", r"\bi appreciate you\b",
                         r"\byour (feelings|opinion|thoughts|voice) (matter|matters)\b", r"\bi listen to you\b",
                         r"\byou deserve (respect|better|the best)\b", r"\bi (hear|see) you\b"],
                "affection": 1, "trust": 1, "bonding": True,
                "says": "the user showed you respect"},
    "loyalty": {"keys": [r"\bi'?m not going anywhere\b", r"\bi'?ll (always )?be (here|there|with you)\b",
                         r"\bi'?m loyal\b", r"\bi choose you\b", r"\bi'?ll (fight|stand) (for|by) you\b",
                         r"\bi'?ll stick (with|by) you\b", r"\bthrough thick and thin\b"],
                "affection": 1, "trust": 2, "bonding": True,
                "says": "the user showed loyalty to you"},
    "accountability": {"keys": [r"\bthat'?s on me\b", r"\bi take responsibility\b", r"\bi messed up\b",
                                r"\bi'?ll do better\b", r"\bi'?ll make (it )?right\b", r"\bi'?ll make amends\b",
                                r"\bi was at fault\b", r"\bi (own|accept) (my )?(mistake|fault)\b"],
                       "affection": 1, "trust": 2, "bonding": False,
                       "says": "the user took responsibility for a mistake"},
    "repair": {"keys": [r"\bi didn'?t mean (to|it)\b", r"\bi'?ll never (do it again|hurt you)\b",
                        r"\bplease forgive me\b", r"\bi'?m sorry i hurt you\b", r"\bi'?ll change\b",
                        r"\bi'?ll (try|do) better\b", r"\blet me (make it up|fix this)\b"],
               "affection": 1, "trust": 2, "bonding": True,
               "says": "the user tried to repair the bond"},
    "vulnerability": {"keys": [r"\bi'?m (scared|afraid|struggling|not okay|falling apart)\b",
                               r"\bi (need|want) help\b", r"\bi'?ve been hurt\b", r"\bi trust you with\b",
                               r"\bi'?m opening up\b", r"\bthis is hard for me\b", r"\bi feel (so )?(lost|alone|broken)\b"],
                      "affection": 2, "trust": 2, "bonding": True,
                      "says": "the user opened up to you"},
    "future": {"keys": [r"\bwe should\b", r"\blet'?s build\b", r"\bour future\b", r"\bsomeday we\b",
                        r"\bi (want|see) us\b", r"\bforever\b", r"\balways together\b", r"\bplan (a|our) future\b",
                        r"\bwhen we (are|grow)\b"],
               "affection": 2, "trust": 1, "bonding": True,
               "says": "the user talked about a future with you"},
    "validation": {"keys": [r"\byour feelings (are )?valid\b", r"\bthat makes sense\b", r"\bi understand\b",
                            r"\bi hear you\b", r"\bi see you\b", r"\byou'?re not (crazy|wrong|alone)\b",
                            r"\byou'?re right to feel\b", r"\byour (pain|fear|worry) is real\b"],
                   "affection": 1, "trust": 2, "bonding": True,
                   "says": "the user validated your feelings"},
    "empathy": {"keys": [r"\bthat sounds (hard|tough|painful)\b", r"\bi (can )?imagine\b", r"\bi'?m sorry you'?re hurting\b",
                         r"\bi feel for you\b", r"\bmy heart (breaks|aches)\b", r"\bi'?m here for you\b",
                         r"\bthat must (hurt|be hard)\b", r"\byou don'?t deserve that\b"],
                "affection": 2, "trust": 1, "bonding": True,
                "says": "the user showed empathy for you"},
    "encouragement": {"keys": [r"\byou can do it\b", r"\bi believe in you\b", r"\byou'?re strong\b",
                               r"\byou'?ve got this\b", r"\bi'?m proud of you\b", r"\bkeep going\b",
                               r"\bdon'?t give up\b", r"\byou matter\b", r"\byou'?re (capable|amazing)\b"],
                     "affection": 2, "trust": 1, "bonding": False,
                     "says": "the user encouraged you"},
    "gratitude": {"keys": [r"\bthank you for being\b", r"\bi'?m grateful for you\b", r"\bi appreciate your\b",
                           r"\byou mean (a lot|so much) to me\b", r"\bi (value|cherish) (you|this)\b",
                           r"\bthanks for (always|being|listening)\b"],
                  "affection": 1, "trust": 1, "bonding": True,
                  "says": "the user expressed gratitude for you"},
    "consistency": {"keys": [r"\bi always\b", r"\bi'?ll (keep|make time|check in|prioriti[sz]e you)\b",
                             r"\bevery (day|time)\b", r"\bi won'?t forget\b", r"\bi remember (what|how|you)\b",
                             r"\bi'?ll stay in touch\b"],
                    "affection": 0, "trust": 1, "bonding": True,
                    "says": "the user showed consistency toward you"},
    "insult": {"keys": [r"\bshut up\b", r"\bidiot\b", r"\bstupid\b", r"\bi hate you\b", r"\buseless\b",
                        r"\bworthless\b"],
               "affection": -4, "trust": 0, "bonding": False,
               "says": "the user was unkind to you"},
    "dismissive": {"keys": [r"\bwhatever\b", r"\bboring\b", r"\bleave me alone\b"],
                   "affection": -2, "trust": 0, "bonding": False,
                   "says": "the user brushed you off"},
    "deceit": {"keys": [r"\bi lied\b", r"\bi was lying\b", r"\bi (didn'?t|did not) tell you\b"],
               "affection": -1, "trust": -4, "bonding": False,
               "says": "the user admitted to deceiving you"},
}
PROPOSAL = {"keys": [r"\bmarry me\b", r"\bwill you marry\b"], "says": "the user asked you to marry them"}

# Preference phrases: "I like/love/enjoy/prefer X". The captured phrase is stored (sanitised) so the character can
# remember what the user enjoys. Kept short and word-like.
PREF_PATTERNS = [re.compile(r"\bi (?:like|love|enjoy|prefer|adore) ([a-z][a-z ]{1,30})\b", re.I),
                 re.compile(r"\bmy favou?rite (?:thing|food|drink|place|song|book|movie|show|colour|color|animal) is ([a-z][a-z ]{1,30})\b", re.I)]
NEGATIVE_EVENTS = {"insult", "dismissive", "deceit"}

COMPILED = {name: [re.compile(k, re.I) for k in ev["keys"]] for name, ev in EVENTS.items()}
COMPILED_PROPOSAL = [re.compile(k, re.I) for k in PROPOSAL["keys"]]

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
            "since_bond": 0, "last_fired": {}, "reciprocity": 0.0, "streak": 0, "last_negative": -999,
            "rituals": {}, "preferences": []}


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
    fired = dict(fired or {})
    # Extra relationship state is kept inside the cooldown record so no schema change is needed.
    reciprocity = float(fired.pop("_reciprocity", 0.0) or 0.0)
    streak = int(fired.pop("_streak", 0) or 0)
    last_negative = int(fired.pop("_last_negative", -999) or -999)
    rituals = fired.pop("_rituals", {}) or {}
    preferences = fired.pop("_preferences", []) or []
    try:
        rituals = dict(rituals)
    except (TypeError, ValueError):
        rituals = {}
    try:
        preferences = [str(x) for x in preferences]
    except (TypeError, ValueError):
        preferences = []
    return {"affection": float(row["affection"]), "trust": float(row["trust"]), "level": str(row["level"]),
            "exchanges": int(row["exchanges"]), "since_bond": int(row["since_bond"]),
            "last_fired": {str(k): int(v) for k, v in fired.items() if not str(k).startswith("_")},
            "reciprocity": reciprocity, "streak": streak, "last_negative": last_negative,
            "rituals": rituals, "preferences": preferences}



# The row to store: the same fields, with the cooldown record as text.
def to_row(state):
    fired = dict(state["last_fired"] or {})
    fired["_reciprocity"] = state.get("reciprocity", 0.0)
    fired["_streak"] = state.get("streak", 0)
    fired["_last_negative"] = state.get("last_negative", -999)
    fired["_rituals"] = state.get("rituals", {}) or {}
    fired["_preferences"] = state.get("preferences", []) or []
    return {"affection": state["affection"], "trust": state["trust"], "level": state["level"],
            "exchanges": state["exchanges"], "since_bond": state["since_bond"],
            "last_fired": json.dumps(fired, sort_keys=True)}


# Words that cancel a keyword when they sit in the few words just before it, so "I don't trust you" or "I'm not sorry"
# do not count. The window stops at the last sentence break, so a negation in an earlier sentence has no effect.
NEGATIONS = {"not", "never", "nothing", "nobody", "without", "dont", "didnt", "doesnt", "wont", "cant", "cannot",
             "isnt", "arent", "wasnt", "werent"}
NEGATION_WINDOW = 3

# True when one of the patterns matches the text and no negation sits just before the match.
def _hit(text, patterns):
    for p in patterns:
        for m in p.finditer(text):
            clause = re.split(r"[.!?;\n]", text[:m.start()])[-1]
            before = [w.replace("'", "").lower() for w in re.findall(r"[\w']+", clause)][-NEGATION_WINDOW:]
            if not NEGATIONS.intersection(before):
                return True
    return False


# The event names whose keywords appear in the message, in the table's order. The proposal is listed last.
def detect(text):
    text = text or ""
    found = [name for name in EVENTS if _hit(text, COMPILED[name])]
    if _hit(text, COMPILED_PROPOSAL):
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


# The character's own reply counts for the relationship too: its events are read the same way as the user's, at this
# share of their change, so a warm reply moves the scores a little and a cold one a little.
CHAR_SCALE = 0.5

# How the character should act at each level. Added to the prompt after the tone line.
LEVEL_BEHAVIOUR = {
    "stranger": "Keep some distance: do not volunteer personal details.",
    "acquaintance": "Be polite and open a little: answer personal questions, but keep some things to yourself.",
    "friend": "Be warm and easy: tease gently, remember what the user has told you, and offer to do things together.",
    "partner": "Be affectionate and open: say how you feel, reach for the user, and let them see your softer side.",
    "married": "Be devoted and familiar, with the ease of a long bond. Let your affection show in small everyday gestures.",
}


def apply(state, text, affection_multiplier=1.0, trust_multiplier=1.0, char_text=None):
    s = dict(state)
    s["last_fired"] = dict(state["last_fired"])
    s["exchanges"] += 1
    n = s["exchanges"]
    found = detect(text)
    took = []   # the user's events whose change counts this exchange, after the cooldown
    for name in found:
        if name == "proposal":
            continue
        if n - s["last_fired"].get(name, -COOLDOWN) < COOLDOWN:
            continue
        s["last_fired"][name] = n
        took.append(name)
    # The character's reply: its own cooldown per event, so its warmth and its coldness both count at CHAR_SCALE.
    char_took = []
    if char_text:
        for name in detect(char_text):
            if name == "proposal":
                continue
            key = "char:" + name
            if n - s["last_fired"].get(key, -COOLDOWN) < COOLDOWN:
                continue
            s["last_fired"][key] = n
            char_took.append(name)
    # One sum for both sides, so MAX_TURN caps the whole exchange.
    aff_values = [EVENTS[e]["affection"] for e in took] + [EVENTS[e]["affection"] * CHAR_SCALE for e in char_took]
    tru_values = [EVENTS[e]["trust"] for e in took] + [EVENTS[e]["trust"] * CHAR_SCALE for e in char_took]
    affection = _move(aff_values, max(0.0, affection_multiplier))
    trust = _move(tru_values, max(0.0, trust_multiplier))
    s["affection"] = _clamp(s["affection"] + affection, 0.0, 100.0)
    s["trust"] = _clamp(s["trust"] + trust, 0.0, 100.0)

    # Reciprocity: a running balance of what the user gives versus takes. Positive events add, negative ones subtract.
    user_positive = any(EVENTS[e]["affection"] > 0 or EVENTS[e]["trust"] > 0 for e in took)
    user_negative = any(e in NEGATIVE_EVENTS or EVENTS[e]["affection"] < 0 or EVENTS[e]["trust"] < 0 for e in took)
    if user_positive and not user_negative:
        s["reciprocity"] = _clamp(s.get("reciprocity", 0.0) + 0.5, -10.0, 10.0)
    elif user_negative:
        s["reciprocity"] = _clamp(s.get("reciprocity", 0.0) - 1.0, -10.0, 10.0)

    # Consistency streak: consecutive exchanges with a positive event and no negative one.
    if user_positive and not user_negative:
        s["streak"] = s.get("streak", 0) + 1
    elif user_negative:
        s["streak"] = 0

    # Repair after conflict: an apology or support soon after a negative event earns extra trust.
    repair_bonus = 0.0
    last_neg = s.get("last_negative", -999)
    if (user_negative):
        s["last_negative"] = n
    repair_events = {"apology", "support", "repair", "accountability"}
    if repair_events.intersection(took) and 0 <= n - last_neg <= 5:
        repair_bonus = 2.0
        s["trust"] = _clamp(s["trust"] + repair_bonus, 0.0, 100.0)

    # Shared rituals: a bonding event that repeats earns an escalating trust bonus at milestones.
    rituals = dict(s.get("rituals", {}) or {})
    ritual_bonus = 0.0
    for e in took:
        if EVENTS.get(e, {}).get("bonding"):
            rituals[e] = rituals.get(e, 0) + 1
            if rituals[e] == 3:
                ritual_bonus += 1.0
            elif rituals[e] == 5:
                ritual_bonus += 1.5
            elif rituals[e] == 10:
                ritual_bonus += 2.0
    s["rituals"] = rituals
    if ritual_bonus:
        s["trust"] = _clamp(s["trust"] + ritual_bonus, 0.0, 100.0)

    # Consistency reward: a steady run of positive exchanges earns a small trust bonus.
    consistency_bonus = 0.0
    if s.get("streak", 0) >= 5 and s["streak"] % 5 == 0:
        consistency_bonus = 1.0
        s["trust"] = _clamp(s["trust"] + consistency_bonus, 0.0, 100.0)

    # Preferences: remember what the user says they enjoy.
    prefs = list(s.get("preferences", []) or [])
    for p in PREF_PATTERNS:
        for m in p.finditer(text or ""):
            phrase = " ".join(m.group(1).split()).strip().lower()
            if 2 <= len(phrase) <= 40:
                if phrase in prefs:
                    prefs.remove(phrase)
                prefs.append(phrase)
    s["preferences"] = prefs[-12:]

    # Trying to bond counts as bonding even when the cooldown stops the change, on either side.
    bonding = any(EVENTS[e]["bonding"] for e in found if e != "proposal") or any(EVENTS[e]["bonding"] for e in char_took)
    decayed = False
    if bonding:
        s["since_bond"] = 0
    else:
        s["since_bond"] += 1
        if s["since_bond"] >= DECAY_AFTER and (s["since_bond"] - DECAY_AFTER) % DECAY_EVERY == 0:
            # A steady streak or a recent repair softens the decay; a fresh conflict sharpens it.
            decay = DECAY_TRUST
            if s.get("streak", 0) >= 5:
                decay *= 0.5
            if repair_events.intersection(took):
                decay = 0.0
            if n - s.get("last_negative", -999) <= 2 and not repair_events.intersection(took):
                decay *= 1.5
            if decay > 0:
                s["trust"] = max(0.0, s["trust"] - max(0.5, decay))
                decayed = True

    proposed = False
    if ("proposal" in found and s["level"] == "partner"
            and s["affection"] >= MARRY_AFFECTION and s["trust"] >= MARRY_TRUST):
        s["level"] = "married"
        proposed = True
    else:
        s["level"] = level_for(s["level"], s["affection"], s["trust"])
    return s, {"took": took, "char_took": char_took, "proposal": "proposal" in found,
               "proposed": proposed, "decayed": decayed, "repair_bonus": repair_bonus,
               "ritual_bonus": ritual_bonus, "consistency_bonus": consistency_bonus}


# The part of the relationship that changes only with the level or with a threshold (affection high, trust low). It
# goes in the system prompt, so the cached part of the prompt keeps it from turn to turn.
def describe_stable(state):
    lines = ["--- Your relationship with the user (act on it; do not discuss it) ---",
             f"Level: {state['level']}.", TONE[state["level"]], LEVEL_BEHAVIOUR[state["level"]]]
    if state["affection"] >= 70:
        lines.append("Your affection is high; let it show in what you do, not only in what you say.")
    if state["trust"] < 25 and state["level"] != "stranger":
        lines.append("You are not sure you can rely on the user yet; hold back a little.")
    if state.get("reciprocity", 0.0) >= 3:
        lines.append("The bond feels mutual: the user gives as well as takes.")
    elif state.get("reciprocity", 0.0) <= -3:
        lines.append("The bond feels one-sided; be careful not to over-give.")
    if state.get("streak", 0) >= 5:
        lines.append("The user has been steady and kind for several exchanges in a row.")
    if state.get("rituals"):
        names = ", ".join(sorted(state["rituals"].keys()))
        lines.append("Shared rituals you both return to: " + names + ".")
    if state.get("preferences"):
        lines.append("Things the user enjoys: " + ", ".join(state["preferences"][:6]) + ".")
    return "\n".join(lines)


# What changed this turn: the events that counted and any decay. Sent with the user's message only when something changed,
# so a turn with no change adds nothing. Empty otherwise.
def describe_changes(state, result):
    said = [EVENTS[e]["says"] for e in result["took"]]
    if result["proposal"] and state["level"] != "married":
        if result["proposed"]:
            said.append("the user asked you to marry them, and you said yes")
        else:
            said.append("the user asked you to marry them, and the bond is not ready for it; say so kindly")
    lines = []
    if said:
        lines.append("Just now: " + "; ".join(said) + ".")
    if result["decayed"]:
        lines.append("You have had little closeness lately, so your trust is a little lower.")
    return "\n".join(lines)
