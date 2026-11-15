# Per-account gallery of generated scene images. Each account keeps its own folder under its data
# directory, with one JSON index and one image file per card. Nothing here is public: app.py serves an
# image only after checking the signed-in account, and ids are checked before touching the disk.
import json
import os
import re
import secrets
import threading
from datetime import datetime, timezone

DIR = "gallery"
INDEX = "gallery.json"
MAX_ITEMS = 200
_ID = re.compile(r"^[0-9a-f]{16}$")
_lock = threading.Lock()


def _root(user_dir):
    return os.path.join(user_dir, DIR)


def _read(user_dir):
    try:
        with open(os.path.join(_root(user_dir), INDEX), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict) and _ID.fullmatch(str(item.get("id") or ""))]


def _write(user_dir, items):
    root = _root(user_dir)
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, INDEX)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _file_for(root, item_id):
    for ext in (".jpg", ".png", ".webp"):
        path = os.path.join(root, item_id + ext)
        if os.path.isfile(path):
            return path
    return None


def add(user_dir, data, mime, character, context, description):
    # Save one generated scene. The picture is already checked and re-encoded by vision.prepare_image.
    with _lock:
        root = _root(user_dir)
        os.makedirs(root, exist_ok=True)
        item_id = secrets.token_hex(8)
        ext = {"image/png": ".png", "image/webp": ".webp"}.get(mime, ".jpg")
        path = os.path.join(root, item_id + ext)
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
        item = {
            "id": item_id,
            "character": str(character or "")[:120],
            "context": str(context or "")[:1200],
            "description": str(description or "")[:1200],
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "mime": mime if mime in ("image/jpeg", "image/png", "image/webp") else "image/jpeg",
        }
        items = _read(user_dir)
        items.insert(0, item)
        for old in items[MAX_ITEMS:]:
            old_path = _file_for(root, old["id"])
            if old_path:
                try:
                    os.remove(old_path)
                except OSError:
                    pass
        _write(user_dir, items[:MAX_ITEMS])
        return item


def list_items(user_dir):
    return _read(user_dir)


def image_path(user_dir, item_id):
    # (path, mime) for one card, or KeyError when the id is not this gallery's.
    if not _ID.fullmatch(str(item_id or "")):
        raise KeyError(item_id)
    root = _root(user_dir)
    for item in _read(user_dir):
        if item["id"] == item_id:
            path = _file_for(root, item_id)
            if path:
                return path, item.get("mime") or "image/jpeg"
    raise KeyError(item_id)


def remove(user_dir, item_id):
    with _lock:
        if not _ID.fullmatch(str(item_id or "")):
            raise KeyError(item_id)
        items = _read(user_dir)
        keep = [item for item in items if item["id"] != item_id]
        if len(keep) == len(items):
            raise KeyError(item_id)
        path = _file_for(_root(user_dir), item_id)
        if path:
            try:
                os.remove(path)
            except OSError:
                pass
        _write(user_dir, keep)
