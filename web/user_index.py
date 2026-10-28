# Shared storage for an account's own index files: one JSON file per feature, kept in the owner's folder.
# Groups, novels and personas all keep an {"items": [...]} index there and all write it the same way, so the
# reading, atomic writing and name tidying live here instead of being copied into each feature.
import json
import os


def user_dir(data_dir, uid):
    return os.path.join(data_dir, "users", str(uid))


def index_path(data_dir, uid, index):
    return os.path.join(user_dir(data_dir, uid), index)


def item_path(data_dir, uid, item_id):
    return os.path.join(user_dir(data_dir, uid), item_id)


# Reads a JSON file, or None when it is missing or damaged.
def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


# Reads the raw entries of an account's index. A missing or damaged file reads as no entries, not a broken page.
# Entries that are not objects are dropped; each feature decides what an entry it can use looks like.
def read_items(data_dir, uid, index):
    data = read_json(index_path(data_dir, uid, index))
    if not isinstance(data, dict):
        return []
    raw = data.get("items")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def write_items(data_dir, uid, index, items):
    write_json(index_path(data_dir, uid, index), {"items": items})


# Writes beside the target and swaps, so a crash mid-write never leaves half a file behind.
def write_json(path, payload):
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def write_text(path, text):
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


# Squashes runs of whitespace and caps the length, so a pasted block cannot break a sidebar entry.
def clean_name(name, limit):
    return " ".join(str(name or "").split())[:limit]


def find_by_id(items, item_id):
    for item in items:
        if item.get("id") == item_id:
            return item
    return None
