# Automatic Mind Palace entries. Every few turns the character reads the recent chat and may suggest up to
# three new entries, and may correct entries it added before. Only the suggestion whose keys come up most in
# the rolling window is kept. When the automatic entries reach their limit, the model must name one of them to
# replace. The user's own entries are never changed. Entries the model adds are marked automatic, so the
# editor shows them and the user can remove or change them.
import copy
import json
import os
import re

from web import palace

CANDIDATES = 3          # new entries the model may suggest per check
CORRECTIONS = 2         # corrections to automatic entries per check
WINDOW = palace.DEPTH_MAX
STATE_FILE = "palace_state.json"

SYSTEM = (
    "You keep a character's Mind Palace in step with a roleplay chat. The Mind Palace is a set of short entries. "
    "Each entry has keys, which are words that bring it into the prompt when they appear in the chat, and content, "
    "which is what the character should remember. You may suggest up to three new entries, and you may correct "
    "automatic entries. Suggest only facts the chat has stated and the character will need later, such as names, "
    "places, promises, preferences, relationships and past events. Skip passing details and anything the user only "
    "hints at. Keys must be words that appear in the chat. Content is one to three sentences in the third person. "
    "Do not repeat the user's own entries. Correct an automatic entry only when the chat contradicts or updates what "
    "it says. Reply with JSON only, in this shape: "
    '{"suggestions": [{"title": "", "keys": [""], "content": "", "replace": ""}], '
    '"corrections": [{"id": "", "content": "", "keys": []}]}. '
    "Use empty lists when there is nothing to do. Leave replace empty unless the prompt says the automatic entries "
    "are full."
)


# Counts a chat turn for this account's character. Returns True on the turn the check is due.
def due(pdir, every):
    path = os.path.join(pdir, STATE_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            turns = int(json.load(f).get("turns", 0))
    except (OSError, ValueError, AttributeError):
        turns = 0
    turns += 1
    ready = turns >= every
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"turns": 0 if ready else turns}, f)
    return ready


# The prompt for one check: the user's entries (titles and keys only, read-only), the automatic entries with ids,
# and whether the automatic entries are full.
def build_prompt(entries, limit):
    auto = [e for e in entries if e["auto"]]
    manual = [e for e in entries if not e["auto"]]
    full = len(auto) >= limit
    lines = ["Your own entries (you cannot change these, and must not repeat them):"]
    for e in manual:
        lines.append(f"- {e['title'] or 'Untitled'}: keys {', '.join(e['keys']) or 'always on'}")
    lines.append("Automatic entries (you may correct these):")
    for e in auto:
        lines.append(f"- id {e['id']}: {e['title'] or 'Untitled'}; keys {', '.join(e['keys'])}; content: {e['content']}")
    if not auto:
        lines.append("- none yet")
    if full:
        lines.append(f"The automatic entries are full ({len(auto)} of {limit}). Each suggestion must name one "
                     "automatic entry id in replace, the entry to remove for it.")
    else:
        lines.append(f"There is room for {limit - len(auto)} more automatic entries. Leave replace empty.")
    lines.append("Write the palace check now, as JSON only.")
    return "\n".join(lines), full


# The first JSON object in the model's reply, or an empty dict when there is none.
def parse(text):
    if not text:
        return {}
    found = re.search(r"\{.*\}", text, re.S)
    if not found:
        return {}
    try:
        data = json.loads(found.group(0))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


# Keys as a clean list of words or phrases, at most the palace's key limit.
def _keys(value):
    if not isinstance(value, list):
        return []
    out = []
    for k in value:
        k = str(k).strip()[:palace.KEY_MAX]
        if k and k.lower() not in {x.lower() for x in out}:
            out.append(k)
    return out[:palace.MAX_KEYS]


# Applies one check's reply to the palace. Returns what changed: the added title, the replaced title, and the
# edited titles. Saves only when something changed.
def apply(pdir, reply, window, entries, cfg):
    limit = cfg["auto"]["limit"]
    entries = copy.deepcopy(entries)
    auto_by_id = {e["id"]: e for e in entries if e["auto"] and e["id"]}
    taken = {k.lower() for e in entries for k in e["keys"]}
    full = len(auto_by_id) >= limit
    summary = {"added": None, "replaced": None, "edited": []}

    best = None
    for cand in (reply.get("suggestions") or [])[:CANDIDATES]:
        if not isinstance(cand, dict):
            continue
        keys = _keys(cand.get("keys"))
        content = str(cand.get("content") or "").strip()[:palace.CONTENT_MAX]
        title = str(cand.get("title") or "").strip()[:palace.TITLE_MAX]
        replace = str(cand.get("replace") or "").strip()
        if not keys or not content or any(k.lower() in taken for k in keys):
            continue
        if full and replace not in auto_by_id:
            continue
        if not full and len(entries) >= palace.MAX_ENTRIES:
            continue
        # The candidate whose keys come up most in the rolling window is the one kept. Zero means it never came up.
        score = palace.count_hits(keys, window)
        if score < 1:
            continue
        if best is None or score > best[0]:
            best = (score, title, keys, content, replace)

    if best is not None:
        _, title, keys, content, replace = best
        if full:
            summary["replaced"] = auto_by_id[replace]["title"] or auto_by_id[replace]["keys"][0]
            entries = [e for e in entries if e["id"] != replace]
        entries.append({
            "id": palace.new_id(), "auto": True, "title": title, "keys": keys, "secondary": [],
            "logic": "and_any", "content": content, "constant": False, "enabled": True, "whole": True,
            "depth": palace.DEFAULT_DEPTH, "order": 100,
        })
        summary["added"] = title or keys[0]

    for fix in (reply.get("corrections") or [])[:CORRECTIONS]:
        if not isinstance(fix, dict):
            continue
        target = auto_by_id.get(str(fix.get("id") or "").strip())
        if target is None or (best is not None and full and target["id"] == best[4]):
            continue
        content = str(fix.get("content") or "").strip()[:palace.CONTENT_MAX]
        keys = _keys(fix.get("keys"))
        if not content and not keys:
            continue
        if content:
            target["content"] = content
        if keys:
            target["keys"] = keys
        summary["edited"].append(target["title"] or target["keys"][0])

    if summary["added"] or summary["edited"]:
        palace.save(pdir, entries, {"recursive": cfg["recursive"], "budget": cfg["budget"], "auto": cfg["auto"]})
    return summary


# One check, run after a turn: asks the model through the engine, then applies the reply. Returns the summary,
# or None when automatic entries are off.
def run(engine, pdir):
    entries, cfg = palace.load_all(pdir)
    if not cfg["auto"]["enabled"]:
        return None
    window = [str(m.get("text") or "") for m in engine.full_history[-WINDOW:] if m.get("text")]
    prompt, _full = build_prompt(entries, cfg["auto"]["limit"])
    reply = parse(engine.ask(SYSTEM, prompt))
    return apply(pdir, reply, window, entries, cfg)
