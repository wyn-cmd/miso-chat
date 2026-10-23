# Account profile pages: a description, a picture, a background, and whether other accounts may see the
# characters this account has liked. They live under data/users/<id>/account, so a character folder can
# never collide with them and the account's chat data is left alone.
import datetime
import json
import os

from web import editor

FOLDER = "account"
META = "profile.json"
AVATAR = "avatar.webp"
BACKGROUND = "background.jpg"
DESCRIPTION_MAX = 1000


class AccountProfileError(ValueError):
    pass


def root_for(data_dir, uid):
    return os.path.join(data_dir, "users", str(uid), FOLDER)


def _meta_path(data_dir, uid):
    return os.path.join(root_for(data_dir, uid), META)


def clean_description(text):
    return str(text or "").replace("\r\n", "\n").strip()


def read(data_dir, uid):
    # Stored profile, with defaults for an account that has never set one.
    try:
        with open(_meta_path(data_dir, uid), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    return {"description": clean_description(data.get("description")),
            "likes_visible": bool(data.get("likes_visible", False)),
            "updated": str(data.get("updated") or "")}


def _write(data_dir, uid, profile):
    root = root_for(data_dir, uid)
    os.makedirs(root, exist_ok=True)
    data = {"description": clean_description(profile.get("description")),
            "likes_visible": bool(profile.get("likes_visible")),
            "updated": datetime.datetime.now().isoformat(timespec="seconds")}
    path = _meta_path(data_dir, uid)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)
    return data


def update(data_dir, uid, description=None, likes_visible=None):
    # Changes only the fields that were passed. Returns the stored profile.
    profile = read(data_dir, uid)
    if description is not None:
        text = clean_description(description)
        if len(text) > DESCRIPTION_MAX:
            raise AccountProfileError(f"Keep the description to {DESCRIPTION_MAX} characters or fewer.")
        profile["description"] = text
    if likes_visible is not None:
        profile["likes_visible"] = bool(likes_visible)
    return _write(data_dir, uid, profile)


def _write_bytes(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def avatar_path(data_dir, uid):
    path = os.path.join(root_for(data_dir, uid), AVATAR)
    return path if os.path.isfile(path) else None


def save_avatar(data_dir, uid, data):
    # A PNG, JPEG or WebP, cropped square and stored as WebP. Raises editor.EditorError for a bad upload.
    encoded, _ext = editor.portrait_bytes(data)
    _write_bytes(os.path.join(root_for(data_dir, uid), AVATAR), encoded)


def remove_avatar(data_dir, uid):
    path = avatar_path(data_dir, uid)
    if path:
        os.remove(path)


def background_path(data_dir, uid):
    path = os.path.join(root_for(data_dir, uid), BACKGROUND)
    return path if os.path.isfile(path) else None


def save_background(data_dir, uid, data):
    # A PNG, JPEG or WebP, downsized as JPEG. Raises editor.EditorError for a bad upload.
    _write_bytes(os.path.join(root_for(data_dir, uid), BACKGROUND), editor.background_bytes(data))


def remove_background(data_dir, uid):
    path = background_path(data_dir, uid)
    if path:
        os.remove(path)


def version(path):
    # File timestamp for cache busting; 0 when there is no picture.
    try:
        return int(os.path.getmtime(path)) if path else 0
    except OSError:
        return 0
