# Chat branches: a copy of one account's chat with one character or group, forked at a message.
# The main chat stays in its original folder, so existing chats need no move. A branch is a full
# copy, so memory, pinned facts and sandbox files afterwards change only on the branch they were made on.
import copy
import datetime
import json
import os
import re
import secrets
import shutil

FILE = "branches.json"
DIR = "branches"
MAIN = "main"
MAX_BRANCHES = 12
NAME_MAX = 40
MEMORY_FILE = "memory.json"
SUMMARY_FILE = "summary.json"
ID_RE = re.compile(r"b[0-9a-f]{8}")


class BranchError(ValueError):
    pass


def _valid_id(value):
    return isinstance(value, str) and ID_RE.fullmatch(value) is not None


def _read(state_root):
    # A damaged file means the main chat only; it never breaks the chat itself.
    try:
        with open(os.path.join(state_root, FILE), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    items = []
    if isinstance(data, dict):
        for item in data.get("items", []):
            if isinstance(item, dict) and _valid_id(item.get("id")) and os.path.isdir(os.path.join(state_root, DIR, item["id"])):
                items.append({"id": item["id"], "name": str(item.get("name") or "Branch")[:NAME_MAX],
                              "fork_index": int(item.get("fork_index", 0)), "created": str(item.get("created") or "")})
    active = data.get("active") if isinstance(data, dict) else MAIN
    if active != MAIN and active not in {i["id"] for i in items}:
        active = MAIN
    return {"active": active, "items": items}


def _write(state_root, data):
    os.makedirs(state_root, exist_ok=True)
    path = os.path.join(state_root, FILE)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def active_dir(state_root):
    # The folder the engine reads and writes for this chat.
    data = _read(state_root)
    if data["active"] == MAIN:
        return state_root
    return os.path.join(state_root, DIR, data["active"])


def listing(state_root):
    data = _read(state_root)
    return {"active": data["active"], "items": data["items"], "max": MAX_BRANCHES}


def _messages(folder):
    try:
        with open(os.path.join(folder, MEMORY_FILE), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _clean_name(name, count):
    text = " ".join(str(name or "").split())[:NAME_MAX]
    return text or f"Branch {count + 1}"


def fork(state_root, index, name):
    # Copies the active chat up to and including message `index`, then switches to the copy.
    data = _read(state_root)
    if len(data["items"]) >= MAX_BRANCHES:
        raise BranchError(f"A chat can have up to {MAX_BRANCHES} branches. Delete one first.")
    if not isinstance(index, int) or isinstance(index, bool):
        raise BranchError("Pick a message to branch from.")
    src = active_dir(state_root)
    messages = _messages(src)
    if not 0 <= index < len(messages):
        raise BranchError("That message no longer exists.")
    bid = "b" + secrets.token_hex(4)
    while os.path.exists(os.path.join(state_root, DIR, bid)):
        bid = "b" + secrets.token_hex(4)
    dst = os.path.join(state_root, DIR, bid)

    def skip_branches(folder, names):
        # Only the top level holds the branch list and branch folders.
        if os.path.normpath(folder) == os.path.normpath(state_root):
            return [n for n in names if n in (FILE, DIR)]
        return []

    shutil.copytree(src, dst, ignore=skip_branches)
    kept = copy.deepcopy(messages[:index + 1])
    with open(os.path.join(dst, MEMORY_FILE), "w", encoding="utf-8") as f:
        json.dump(kept, f, ensure_ascii=False, indent=2)
    summary = os.path.join(dst, SUMMARY_FILE)
    if os.path.exists(summary):
        try:
            with open(summary, encoding="utf-8") as f:
                covered = int(json.load(f).get("covered", 0))
        except (OSError, ValueError, TypeError, AttributeError):
            covered = 0
        if covered > len(kept):
            # The summary describes messages after the fork point, so the branch rebuilds it.
            os.remove(summary)
    item = {"id": bid, "name": _clean_name(name, len(data["items"])), "fork_index": index,
            "created": datetime.datetime.now().isoformat(timespec="seconds")}
    data["items"].append(item)
    data["active"] = bid
    _write(state_root, data)
    return bid


def switch(state_root, bid):
    data = _read(state_root)
    if bid != MAIN and bid not in {i["id"] for i in data["items"]}:
        raise BranchError("That branch no longer exists.")
    data["active"] = bid
    _write(state_root, data)


def delete(state_root, bid):
    # The main chat cannot be deleted. Deleting the open branch returns to the main chat.
    if bid == MAIN:
        raise BranchError("The main chat cannot be deleted.")
    data = _read(state_root)
    if bid not in {i["id"] for i in data["items"]}:
        raise BranchError("That branch no longer exists.")
    data["items"] = [i for i in data["items"] if i["id"] != bid]
    if data["active"] == bid:
        data["active"] = MAIN
    _write(state_root, data)
    shutil.rmtree(os.path.join(state_root, DIR, bid), ignore_errors=True)
