# Near-lossless token quenching: two stages applied to the chat history before it is sent to the model, for accounts
# that turn it on. Nothing here calls a model, reads a file or writes one.
#
# Stage 1 (strip) removes what carries nothing the model can use: zero-width characters, non-breaking spaces, runs of
# spaces, trailing spaces, three or more blank lines, HTML comments and lines that only decorate (---, ***, ~~~).
# It never removes a word. The engine runs its own safe-word substitutions before this, as the first half of the same
# cheap and safe pass.
#
# Stage 2 (the entropy gate) works on the older messages of the window only. The newest KEEP_RECENT messages, every
# message the account wrote, and every message of fewer than two sentences are left alone. Sentences are ranked by
# surprisal from the window's own word distribution, so the most predictable ones are tried first. A removal is kept
# only when the fidelity gate still passes afterwards: at least FLOOR of the window's distinct content words must
# survive, every number, proper noun and quoted line must survive, and no more than MAX_DROP of the characters may go.
# A removal that fails the gate is refused, and the pass carries on with the next candidate; once nothing passes, the
# text is left as it stands.
#
# The floor is lexical. It bounds lost wording, not proven meaning, so the honest claim is near-lossless rather than
# lossless: the model keeps every fact, figure, name and quoted line, and at least nine words in ten of the rest.

import math
import re

FLOOR = 0.92          # least share of the window's distinct content words that must survive a removal
MAX_DROP = 0.35       # most of the window's characters that may be removed
KEEP_RECENT = 4       # the newest messages are never compressed
MIN_WORDS = 6         # a sentence with fewer content words is a beat, not filler, and is kept

ZERO_WIDTH = re.compile("[\u200b-\u200d\u2060\ufeff]")
COMMENT = re.compile(r"<!--.*?-->", re.S)
DIVIDER = re.compile(r"^[\s]*(?:[-*_~=]{3,}|\.{3,})[\s]*$")
MULTI_NEWLINE = re.compile(r"\n{3,}")
MULTI_SPACE = re.compile(r"[ \t]{2,}")
WORD = re.compile(r"[^\W\d_][\w'\u2019-]*", re.UNICODE)
NUMBER = re.compile(r"[$€£]?\d[\d.,]*%?")
QUOTED = re.compile(r"[\"\u201c][^\"\u201d]{3,}[\"\u201d]|[\u2018][^\u2019]{3,}[\u2019]")
SENTENCE = re.compile(r"[^.!?\n]+[.!?]+[\"\u201d\u2019)]*|[^.!?\n]+$")

# Function words only. Words that carry the scene (places, names, objects, feelings) are never in this list, because
# dropping them is exactly what the fidelity floor exists to prevent.
STOPWORDS = frozenset("""
a about above after again against all am an and any are aren't as at be because been before being below between both
but by can cannot could couldn't did didn't do does doesn't doing don't down during each few for from further had
hadn't has hasn't have haven't having he he'd he'll he's her here here's hers herself him himself his how how's i i'd
i'll i'm i've if in into is isn't it it's its itself let's me more most mustn't my myself no nor not of off on once
only or other ought our ours ourselves out over own same shan't she she'd she'll she's should shouldn't so some such
than that that's the their theirs them themselves then there there's these they they'd they'll they're they've this
those through to too under until up very was wasn't we we'd we'll we're we've were weren't what what's when when's
where where's which while who who's whom why why's with won't would wouldn't you you'd you'll you're you've your
yours yourself yourselves also just like really quite still even much many
""".split())


# Stage 1: the cheap, safe pass. Whitespace and decoration only.
def strip(text):
    t = ZERO_WIDTH.sub("", str(text or ""))
    t = t.replace("\u00a0", " ").replace("\r\n", "\n").replace("\r", "\n")
    t = COMMENT.sub("", t)
    kept = []
    for line in t.split("\n"):
        line = MULTI_SPACE.sub(" ", line).rstrip()
        if DIVIDER.match(line):
            continue
        kept.append(line)
    return MULTI_NEWLINE.sub("\n\n", "\n".join(kept)).strip()


# The words that carry meaning: not stopwords, not numerals, no very short tokens.
def _content(text):
    return [w.lower() for w in WORD.findall(text or "") if len(w) > 2 and w.lower() not in STOPWORDS]


# Word counts across the window, which stand in for the window's language model in the surprisal estimate.
def _counts(texts):
    counts = {}
    for t in texts:
        for w in _content(t):
            counts[w] = counts.get(w, 0) + 1
    return counts


# Add-half smoothing: a word the window has not used is rare, so it is costly to remove and unlikely to be a candidate.
def _probs(counts):
    total = sum(counts.values())
    denom = total + 0.5 * (len(counts) + 1)
    return {w: (c + 0.5) / denom for w, c in counts.items()}, 0.5 / denom


# Mean surprisal per content word, in bits. A low figure means a sentence says little that the window has not
# already said, which is the entropy signal the gate uses.
def _density(sentence, probs, unseen):
    words = _content(sentence)
    if not words:
        return 0.0
    return sum(-math.log2(probs.get(w, unseen)) for w in words) / len(words)


# What a removal must never take away: figures, money, percentages, proper nouns, shouted words and quoted speech.
def _anchors(text):
    found = set(NUMBER.findall(text or ""))
    found.update(m.strip() for m in QUOTED.findall(text or ""))
    for m in WORD.finditer(text or ""):
        word = m.group(0)
        if len(word) >= 3 and word.isupper():
            found.add(word)
            continue
        if not word[:1].isupper() or m.start() == 0:
            continue
        before = (text or "")[:m.start()].rstrip()
        if before and before[-1] not in ".!?\n\"\u201c\u201d":
            found.add(word)
    return found


# The sentences of one message, as the exact substrings they occupy, so a removal leaves the rest of the text as it was.
def _sentences(text):
    return [m.group(0) for m in SENTENCE.finditer(text or "") if m.group(0).strip()]


# Removes one sentence and tidies the seam it leaves. None when the sentence is no longer there.
def _cut(text, sentence):
    at = text.find(sentence)
    if at < 0:
        return None
    merged = (text[:at] + text[at + len(sentence):]).strip()
    merged = MULTI_SPACE.sub(" ", merged)
    return merged if merged else None


# The gate itself: the share of the window's distinct content words still present, and every anchor still present.
def _passes(vocab, anchors, texts, floor):
    present = set()
    for t in texts:
        present.update(_content(t))
    if vocab and len(present & vocab) / len(vocab) < floor:
        return False
    return anchors <= _anchors("\n".join(texts))


def _ratio(vocab, texts):
    if not vocab:
        return 1.0
    present = set()
    for t in texts:
        present.update(_content(t))
    return len(present & vocab) / len(vocab)


def _report(before, after, removed, blocked, fidelity):
    before_chars = sum(len(t) for t in before)
    after_chars = sum(len(t) for t in after)
    return {"on": True, "before": before_chars, "after": after_chars, "removed": removed, "blocked": blocked,
            "fidelity": round(fidelity, 3), "saved_tokens": max(0, (before_chars - after_chars + 3) // 4)}


# The second stage. texts is the window in order, oldest first. protect marks messages that must not lose a sentence,
# which the engine sets for the account's own messages. Returns the new texts and a report for the context marker.
def gate(texts, protect=None, floor=FLOOR):
    original = [str(t or "") for t in texts]
    work = list(original)
    n = len(work)
    guard = list(protect)[:n] if protect is not None else []
    guard += [False] * (n - len(guard))
    if n <= KEEP_RECENT or not any(t.strip() for t in work):
        return work, _report(original, work, 0, 0, 1.0)

    counts = _counts(work)
    if not counts:
        return work, _report(original, work, 0, 0, 1.0)
    probs, unseen = _probs(counts)

    # Candidates: a sentence in an older message, long enough to hold filler, carrying no anchor, in a message that
    # will still have a sentence left if this one goes.
    rows = []
    for i in range(n - KEEP_RECENT):
        if guard[i]:
            continue
        sentences = _sentences(work[i])
        if len(sentences) < 2:
            continue
        for sentence in sentences:
            if len(_content(sentence)) < MIN_WORDS or _anchors(sentence):
                continue
            rows.append((_density(sentence, probs, unseen), i, sentence))
    if not rows:
        return work, _report(original, work, 0, 0, 1.0)

    queue = sorted(rows, key=lambda r: (r[0], r[1]))

    vocab = set()
    for t in work:
        vocab.update(_content(t))
    anchors = _anchors("\n".join(work))
    budget = sum(len(t) for t in work) * (1.0 - MAX_DROP)
    removed = blocked = 0
    for _density_value, i, sentence in queue:
        cut = _cut(work[i], sentence)
        if cut is None:
            continue
        trial = list(work)
        trial[i] = cut
        # The budget is checked on the trial, so the last accepted cut never takes the window past MAX_DROP.
        if sum(len(t) for t in trial) < budget:
            blocked += 1
            continue
        if not _passes(vocab, anchors, trial, floor):
            blocked += 1
            continue
        work = trial
        removed += 1

    return work, _report(original, work, removed, blocked, _ratio(vocab, work))
