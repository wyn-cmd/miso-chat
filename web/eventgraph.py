# The event memory graph: what happened to a character, in time order, each event with the belief it led to and the
# consequence it had. The nightly dream writes it from recent chat, never during a turn. It sits beside the memory
# bank and does not replace it. The character card and pinned facts are not touched here.
#
# Two layers. The stable layer is the character card, which this module never changes. The evolving layer is the
# list of EVOLVE lines, short notes on how the character has changed because of what happened to it. Events are
# recalled into a turn's prompt by word overlap with the latest messages, the way palace keys are matched.
#
# Based on the ABC model (activating event, belief, consequence) from the DREAM paper, adapted from literary source
# text to live chat.
import json
import os
import re
import tempfile

FILE = "events.json"
MAX_NODES = 200          # events kept; the oldest go first, with their links
MAX_EVOLVE = 12          # evolving lines kept; the oldest go first
MAX_NEW_EVENTS = 5       # most EVENT lines a single dream may add
NEW_MESSAGES = 40        # most messages since the last pass that a dream reads for events
RECENT_FOR_DREAM = 8     # latest events shown to the dream, so it can link a new event to one of them
PROMPT_EVENTS = 4        # events recalled into one turn's prompt
FIELD_MAX = 240

STOP = {
    "about", "above", "after", "again", "against", "because", "before", "being", "could", "every", "from", "have",
    "into", "just", "like", "more", "much", "only", "other", "over", "really", "should", "some", "than", "that",
    "their", "them", "then", "there", "these", "they", "this", "those", "through", "very", "well", "were", "what",
    "when", "where", "which", "while", "will", "with", "would", "your", "yours", "theirs",
}


# An empty graph, or the stored one with any missing parts filled in.
def empty():
    return {"next": 0, "covered": 0, "nodes": [], "edges": [], "evolving": []}


# Reads a character's graph. A damaged file means an empty graph, not a broken chat.
def load(path):
    graph = empty()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return graph
    if not isinstance(data, dict):
        return graph
    graph["next"] = int(data.get("next", 0) or 0)
    graph["covered"] = int(data.get("covered", 0) or 0)
    graph["nodes"] = [n for n in data.get("nodes", []) if isinstance(n, dict) and n.get("id")]
    graph["edges"] = [e for e in data.get("edges", []) if isinstance(e, dict) and e.get("from") and e.get("to")]
    graph["evolving"] = [e for e in data.get("evolving", []) if isinstance(e, dict) and e.get("text")]
    return graph


# Writes the graph atomically, so a crash mid-write keeps the last good file.
def save(path, graph):
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix="events-", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        json.dump(graph, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# Reads EVENT and EVOLVE lines from a dream's reply. An EVENT line is
#   EVENT: actor | what happened | what the character came to believe | what it led to | after: e3
# where the last field is optional and names an earlier event this one follows from. Anything else is ignored.
def parse_events(text):
    events, evolve = [], []
    for raw in (text or "").splitlines():
        found = re.match(r"^(EVENT|EVOLVE)\s*:\s*(.*)$", raw.strip(), re.I)
        if not found:
            continue
        kind, payload = found.group(1).upper(), found.group(2).strip()
        if kind == "EVOLVE":
            if payload and len(evolve) < MAX_NEW_EVENTS:
                evolve.append(payload[:FIELD_MAX])
            continue
        if len(events) >= MAX_NEW_EVENTS:
            continue
        parts = [p.strip() for p in payload.split("|")]
        if len(parts) < 4 or not all(parts[:4]):
            continue
        after = None
        for extra in parts[4:]:
            if extra.lower().startswith("after:"):
                after = extra[6:].strip() or None
        events.append({"actor": parts[0][:60], "event": parts[1][:FIELD_MAX], "belief": parts[2][:FIELD_MAX],
                       "consequence": parts[3][:FIELD_MAX], "after": after})
    return events, evolve


# Adds a dream's events and evolving lines. Each event follows the one before it in time, and links to an earlier
# event when the dream named one that exists. Returns (events added, evolving lines added).
def apply(graph, events, evolve, when):
    ids = {n["id"] for n in graph["nodes"]}
    added = 0
    for ev in events:
        graph["next"] += 1
        node = {"id": f"e{graph['next']}", "when": when,
                "actor": ev["actor"], "event": ev["event"], "belief": ev["belief"], "consequence": ev["consequence"]}
        if graph["nodes"]:
            graph["edges"].append({"from": graph["nodes"][-1]["id"], "to": node["id"], "rel": "then"})
        if ev["after"] in ids:
            graph["edges"].append({"from": ev["after"], "to": node["id"], "rel": "caused"})
        graph["nodes"].append(node)
        ids.add(node["id"])
        added += 1
    for line in evolve:
        graph["evolving"].append({"text": line, "when": when})
    _trim(graph)
    return added, len(evolve)


# Keeps the graph within its limits. Links to a removed event go with it.
def _trim(graph):
    if len(graph["nodes"]) > MAX_NODES:
        graph["nodes"] = graph["nodes"][-MAX_NODES:]
        kept = {n["id"] for n in graph["nodes"]}
        graph["edges"] = [e for e in graph["edges"] if e["from"] in kept and e["to"] in kept]
    graph["evolving"] = graph["evolving"][-MAX_EVOLVE:]


# The latest events, as short lines for the dream to link against.
def recent_for_dream(graph):
    return [f"id {n['id']}: {n['actor']} {n['event']}" for n in graph["nodes"][-RECENT_FOR_DREAM:]]


def _words(text):
    return {w for w in re.findall(r"[a-z']{4,}", (text or "").lower()) if w not in STOP}


# The events that bear on the given texts, in time order, by how many words they share with them. Returns the lines
# and the evolving lines.
def recall(graph, texts):
    wanted = set()
    for t in texts:
        wanted |= _words(t)
    scored = []
    for node in graph["nodes"]:
        bag = _words(" ".join(node.get(k, "") for k in ("actor", "event", "belief", "consequence")))
        hits = len(bag & wanted)
        if hits:
            scored.append((hits, node))
    top = sorted(scored, key=lambda item: -item[0])[:PROMPT_EVENTS]
    chosen = sorted((node for _, node in top), key=lambda n: int(n["id"][1:]) if n["id"][1:].isdigit() else 0)
    lines = [f"- {n['actor']} {n['event']}. This made the character believe: {n['belief']}. "
             f"It led to: {n['consequence']}." for n in chosen]
    return lines, [e["text"] for e in graph["evolving"]]
