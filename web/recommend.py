# Home-page suggestions from one account's chat activity. Counts the messages it has sent to each character,
# then suggests characters it has not tried yet that share tags with the ones it chats with most.
# Reads files only, so it makes no model calls.
import json
import os
from collections import Counter

from web import profiles

LIMIT = 4


def _messages(data_dir, uid, folder):
    try:
        with open(os.path.join(data_dir, "users", str(uid), folder, "memory.json"), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return 0
    return len(data) if isinstance(data, list) else 0


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
