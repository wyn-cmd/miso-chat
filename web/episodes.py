# Episodic memory: when messages leave the recent window, they are cut into short episodes. Each episode keeps
# its text and a few keys (names, places and distinctive words). A later message that touches those keys brings
# the episode back into the prompt, even when the running summary has blurred it. Keys are matched with the Mind
# Palace matcher, so plurals and common endings count.
#
# When the keys find nothing, a similarity search over episode vectors is the fallback. To keep embedding cheap:
# episodes are embedded in one batch when the memory refresh cuts them (not on every message), each message is
# shortened before it is embedded, and a turn embeds only the message being sent, and only when the keys missed.
# The model name and vector size are constants below; check them against the embedding model before relying on
# the figures.
import hashlib
import math
import re
from collections import Counter

from web import palace

EPISODE_SIZE = 6        # messages per episode
MESSAGE_CHARS = 200     # most text kept from each message, so all six messages fit in an episode
EPISODE_CHARS = 1200    # most text kept per episode (about 300 tokens)
MAX_EPISODES = 300      # oldest episodes are dropped past this
KEY_LIMIT = 8           # keys per episode
MATCH_LIMIT = 3         # episodes brought back per turn
BUDGET = 1500           # characters of recalled episodes per turn
SCAN_MESSAGES = 8       # newest messages scanned for keys
EMBED_MODEL = "gemini-embedding-001"
EMBED_DIM = 256         # vector size; smaller is cheaper to store and search
SIM_MIN = 0.3           # the least similarity a vector match must reach; a guess, calibrate on the real model

STOP = set("""
about above after again against all also always among and another any are around because been before being
below between both but came come could did does doing done down during each either else even ever every
from further give going gone good great had has have having here hers him himself his how however into its
itself just keep know like little long made make many maybe more most much must myself near never next none
not nothing now off often okay once only onto other ours out over own quite really right said same say see
she should since some something soon still such sure take tell than that their them then there these they
thing things think this those though through till together too took toward under until upon very want wants
was well were what when where whether which while who whom whose why will with within without would yes
yet you your yours yourself user chat said says just look looks looked feel feels felt
the for are but not can had has her him his how its our out she who why did get got one see yes all any
its off our too via and nor per was were you
""".split())

WORD = re.compile(r"[A-Za-z][A-Za-z'\-]{2,}")


# The keys for one episode: its names and distinctive words, most frequent first. Capitalised words that are
# not the first word of a sentence count as names and rank higher. Speaker names are left out.
def _keys(text, exclude):
    names, words = Counter(), Counter()
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
        tokens = WORD.findall(sentence)
        for i, tok in enumerate(tokens):
            low = tok.lower().strip("'-")
            # A capitalised word mid-sentence is a name, and short names such as Tom or Pip still count.
            name = tok[0].isupper() and i > 0
            if len(low) < (3 if name else 4) or low in STOP or low in exclude:
                continue
            if name:
                names[low] += 1
            else:
                words[low] += 1
    score = Counter()
    for k, n in names.items():
        score[k] += 2 * n
    for k, n in words.items():
        if n >= 2 or k in names:
            score[k] += n
    return [k for k, _ in score.most_common(KEY_LIMIT)]


# Episodes for a run of messages. base is the index of the first message in the full history, so each episode
# records where its messages sit and can be dropped if the history is later shortened.
def make(messages, base, speakers=()):
    exclude = {str(s).lower().strip("'-") for s in speakers if s}
    out = []
    for i in range(0, len(messages), EPISODE_SIZE):
        chunk = messages[i:i + EPISODE_SIZE]
        lines = []
        for m in chunk:
            text = str(m.get("text") or "").strip()
            if text:
                lines.append(f"{m.get('speaker') or m.get('role', 'user')}: {text[:MESSAGE_CHARS]}")
        body = "\n".join(lines)[:EPISODE_CHARS]
        keys = _keys(body, exclude)
        if body and keys:
            out.append({"start": base + i, "end": base + i + len(chunk), "keys": keys, "text": body})
    return out


# Episodes whose keys come up in the newest messages, as text in the order they happened. Only episodes that
# have already left the window (end <= visible_from) are eligible, because the rest are already in the prompt.
def recall_keywords(episodes, texts, visible_from, budget=BUDGET, limit=MATCH_LIMIT):
    scan = "\n".join(palace._norm(str(t)) for t in texts[-SCAN_MESSAGES:] if t)
    if not scan.strip():
        return []
    scored = []
    for ep in episodes:
        if ep["end"] > visible_from:
            continue
        hits = [k for k in ep["keys"] if palace._any_form([k], scan, True)]
        if hits:
            scored.append((len(hits), ep["end"], ep))
    scored.sort(key=lambda s: (-s[0], -s[1]))
    return _take(scored, budget, limit)


# The same eligibility, by similarity: the episodes whose vector is closest to the query and at least SIM_MIN.
def recall_vectors(episodes, query, visible_from, budget=BUDGET, limit=MATCH_LIMIT):
    scored = []
    for ep in episodes:
        if ep["end"] > visible_from or not ep.get("vec"):
            continue
        sim = cosine(query, ep["vec"])
        if sim >= SIM_MIN:
            scored.append((sim, ep["end"], ep))
    scored.sort(key=lambda s: (-s[0], -s[1]))
    return _take(scored, budget, limit)


# The top episodes within the limit and the budget, as text in chronological order.
def _take(scored, budget, limit):
    chosen, used = [], 0
    for _, _, ep in scored[:limit]:
        if used + len(ep["text"]) > budget:
            continue
        chosen.append(ep)
        used += len(ep["text"])
    chosen.sort(key=lambda e: e["start"])
    return [e["text"] for e in chosen]


# Cosine similarity of two vectors; 0 when either is empty or they differ in size.
def cosine(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


# Vectors for a batch of texts in one call. Returns None when the call fails or the reply is the wrong shape.
# A failure is not an error for the chat: the episodes keep their keys and are still found by keyword.
def embed(client, texts):
    if not texts:
        return []
    from google.genai import types
    try:
        resp = client.models.embed_content(
            model=EMBED_MODEL, contents=list(texts),
            config=types.EmbedContentConfig(output_dimensionality=EMBED_DIM))
        vectors = [[round(float(x), 4) for x in e.values] for e in resp.embeddings]
    except Exception:
        return None
    if len(vectors) != len(texts) or any(len(v) != EMBED_DIM for v in vectors):
        return None
    return vectors


# Rough token count of some texts, at about four characters a token, for the usage budget.
def estimate_tokens(texts):
    return sum(len(t) for t in texts) // 4


# A short key for a query, so the same message is not embedded twice.
def query_key(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


# Drops episodes that reach past a shortened history (after an undo), so they never describe other messages.
def prune(episodes, length):
    return [e for e in episodes if e["end"] <= length]


# Keeps the newest MAX_EPISODES episodes.
def trim(episodes):
    return episodes[-MAX_EPISODES:]
