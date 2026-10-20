# Ordering from one account's chat activity: the home suggestions, the search order and the sidebar.
# A character counts as chatted with once the account has a saved message in it, on the main chat or on a branch.
# Reads files only, so it makes no model calls.
import json
import os
from collections import Counter

from web import branches, profiles

LIMIT = 4


def _count(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return 0
    if not isinstance(data, list):
        return 0
    # Count only what the engine loads (a role and text, or a bare string), so junk entries never make a chat.
    return sum(1 for item in data if isinstance(item, str) or (isinstance(item, dict) and "role" in item and "text" in item))


def _messages(data_dir, uid, folder):
    # The longest saved chat for this character: the main chat or one branch. A branch starts as a copy of
    # the main chat, so the larger count is the right one; adding them would count the same messages twice.
    state_root = os.path.join(data_dir, "users", str(uid), folder)
    counts = [_count(os.path.join(state_root, "memory.json"))]
    for node in branches.tree(state_root)["nodes"]:
        if node["id"] != branches.MAIN:
            counts.append(_count(os.path.join(state_root, branches.DIR, node["id"], "memory.json")))
    return max(counts)


def chatted(data_dir, uid, folders):
    # Characters this account has at least one saved message with. Feeds the sidebar.
    return [f for f in folders if _messages(data_dir, uid, f) > 0]


def _popularity(data_dir, folders):
    # Messages per character across every account. The fallback order for an account with no chats yet.
    total = Counter()
    root = os.path.join(data_dir, "users")
    if not os.path.isdir(root):
        return total
    for uid in os.listdir(root):
        for f in folders:
            total[f] += _messages(data_dir, uid, f)
    return total


def order(char_root, data_dir, uid, folders):
    # Best first: characters sharing tags with this account's chats, then the most used across accounts, then name.
    tags = {}
    for f in folders:
        prof = profiles.read_profile(os.path.join(char_root, f))
        tags[f] = set(prof["tags"]) if prof else set()
    own = {f: _messages(data_dir, uid, f) for f in folders}
    affinity = Counter()
    for f in folders:
        if own[f] > 0:
            for t in tags[f]:
                affinity[t] += own[f]
    popular = _popularity(data_dir, folders)

    def key(f):
        score = sum(affinity[t] for t in tags[f])
        return (-score, -popular[f], f.lower())

    return sorted(folders, key=key)


def recommend(char_root, data_dir, uid, folders, limit=LIMIT):
    # Returns [{"folder": ..., "tags": [shared tags, most relevant first]}], best first.
    tags = {}
    for f in folders:
        prof = profiles.read_profile(os.path.join(char_root, f))
        tags[f] = set(prof["tags"]) if prof else set()
    activity = {f: _messages(data_dir, uid, f) for f in folders}
    if not any(activity.values()):
        return []
    affinity = Counter()
    for f, n in activity.items():
        if n > 0:
            for t in tags[f]:
                affinity[t] += n
    scored = []
    for f in folders:
        if activity[f] > 0 or not tags[f]:
            continue
        shared = sorted((t for t in tags[f] if affinity[t] > 0), key=lambda t: (-affinity[t], t))
        if shared:
            scored.append((sum(affinity[t] for t in shared), f, shared))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [{"folder": f, "tags": shared[:3]} for _, f, shared in scored[:limit]]
