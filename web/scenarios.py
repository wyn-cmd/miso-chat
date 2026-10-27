# Scenarios: an account's own library of opening messages for a chat. A scenario is a short prompt the reader can send
# as the first message. One scenario can be picked for every new chat, or one can be picked at random; either way the
# chat shows the text with a Start button and nothing is sent until the reader presses it.
#
# This module is pure logic plus its own file. It does no model calls and does not touch chat history.
import json
import os
import random
import re
import secrets
import tempfile

FILE = "scenarios.json"
PREFIX = "scn-"
ID_RE = re.compile(r"scn-[0-9a-f]{8}")
MODES = ("off", "fixed", "random")
MAX_SCENARIOS = 30
NAME_MAX = 60
TEXT_MAX = 4000          # the same limit as one chat message
SEEDED = [
    ("A quiet arrival",
     "It is late, and the rain has kept everyone indoors. I step in from the street and shake the water from my "
     "coat, then notice you are already here. \"I didn't think anyone else would be up,\" I say."),
    ("A chance meeting",
     "Our paths cross in a crowded market. I am carrying more than I can hold, and before I can stop it an orange "
     "rolls away across the stones. I look up at you, half laughing. \"Well. That could have gone better.\""),
    ("The door left open",
     "I find the door unlocked and the lights still on, though the house should be empty. I step inside slowly and "
     "call out, \"Hello? Is someone here?\""),
]


class ScenarioError(ValueError):
    pass


# An empty store for one account, before the defaults are seeded.
def empty():
    return {"mode": "off", "fixed": None, "items": []}


# A store holding the three openings that ship with the app.
def seeded():
    store = empty()
    for name, text in SEEDED:
        store["items"].append({"id": _new_id(store), "name": name, "text": text, "created": ""})
    return store


# Reads an account's library, seeding the defaults when the file does not exist yet. An empty library the reader made
# on purpose is kept empty: once the file exists, its contents win.
def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return seeded()
    if not isinstance(data, dict):
        return seeded()
    store = empty()
    store["items"] = [_clean_item(i) for i in data.get("items", []) if _valid_id(i)]
    mode = data.get("mode")
    store["mode"] = mode if mode in MODES else "off"
    fixed = data.get("fixed")
    store["fixed"] = fixed if any(i["id"] == fixed for i in store["items"]) else None
    if store["mode"] == "fixed" and store["fixed"] is None:
        store["mode"] = "off"   # the chosen opening is gone, so no new chat gets one
    return store


# Writes the store atomically, so a crash mid-write keeps the last good file.
def save(path, store):
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix="scenarios-", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _valid_id(item):
    return isinstance(item, dict) and isinstance(item.get("id"), str) and ID_RE.fullmatch(item["id"]) is not None


# Keeps only the known fields, so a hand-edited or older file cannot inject odd keys.
def _clean_item(item):
    return {"id": item["id"], "name": clean_text(item.get("name"), NAME_MAX) or "Scenario",
            "text": clean_scene(item.get("text"), TEXT_MAX), "created": str(item.get("created") or "")}


# One line of text, with whitespace squeezed and an optional length cap.
def clean_text(value, limit=None):
    text = " ".join(str(value or "").split())
    return text if limit is None else text[:limit]


# Multi-line text keeps its line breaks but loses the blank space around them, so a written scene reads as written.
def clean_scene(value, limit=None):
    lines = [" ".join(line.split()) for line in str(value or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    text = "\n".join(lines).strip()
    return text if limit is None else text[:limit]


def _new_id(store):
    taken = {i["id"] for i in store["items"]}
    for _ in range(20):
        candidate = PREFIX + secrets.token_hex(4)
        if candidate not in taken:
            return candidate
    return PREFIX + secrets.token_hex(4)


def get(store, sid):
    for item in store["items"]:
        if item["id"] == sid:
            return item
    return None


def _check(name, text):
    name = clean_text(name, NAME_MAX)
    if not name:
        raise ScenarioError("Give the scenario a name.")
    text = clean_scene(text)
    if not text:
        raise ScenarioError("Write the opening message.")
    if len(text) > TEXT_MAX:
        raise ScenarioError(f"The opening can be up to {TEXT_MAX} characters.")
    return name, text


# Creates a scenario and returns it. The store is changed in place; the caller saves it.
def create(store, name, text, now_iso):
    if len(store["items"]) >= MAX_SCENARIOS:
        raise ScenarioError(f"An account can keep up to {MAX_SCENARIOS} scenarios. Delete one first.")
    name, text = _check(name, text)
    item = {"id": _new_id(store), "name": name, "text": text, "created": now_iso}
    store["items"].append(item)
    return item


# Replaces a scenario's name and text. Its id and creation time are kept.
def update(store, sid, name, text):
    item = get(store, sid)
    if item is None:
        raise ScenarioError("That scenario no longer exists.")
    name, text = _check(name, text)
    item.update({"name": name, "text": text})
    return item


# Removes a scenario. If it was the one picked for new chats, the mode turns off.
def delete(store, sid):
    if get(store, sid) is None:
        raise ScenarioError("That scenario no longer exists.")
    store["items"] = [i for i in store["items"] if i["id"] != sid]
    if store["fixed"] == sid:
        store["fixed"] = None
        if store["mode"] == "fixed":
            store["mode"] = "off"


# Sets how a new chat gets its opening: off, one chosen scenario, or one at random.
def set_mode(store, mode, fixed):
    if mode not in MODES:
        raise ScenarioError("Pick off, one scenario, or random.")
    if mode == "fixed":
        if not fixed or get(store, fixed) is None:
            raise ScenarioError("Pick the scenario for new chats.")
        store["fixed"] = fixed
    else:
        store["fixed"] = None
    store["mode"] = mode


# The scenario a new chat should offer, or None. A missing chosen scenario means none rather than a broken chat.
def choose(store):
    if store["mode"] == "fixed":
        return get(store, store["fixed"]) if store["fixed"] else None
    if store["mode"] == "random" and store["items"]:
        return random.choice(store["items"])
    return None
