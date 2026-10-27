# Account profile pages: a description, a picture, a background, and whether other accounts may see the
# characters this account has liked. They live under data/users/<id>/account, so a character folder can
# never collide with them and the account's chat data is left alone.
import datetime
import json
import os

from web import editor

FOLDER = "account"
META = "profile.json"
# A profile picture in the order it is looked for. avatar.gif is an animated GIF, kept as uploaded.
AVATAR_FILES = ("avatar.webp", "avatar.gif")
# A profile background is a picture, or a video stored as WebM with its poster beside it.
BACKGROUND = "background.png"
BACKGROUND_JPEG = "background.jpg"   # a picture saved before backgrounds were stored as PNG; still read
BACKGROUND_VIDEO = "background.webm"
BACKGROUND_POSTER = "background.poster.jpg"
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
    root = root_for(data_dir, uid)
    for name in AVATAR_FILES:
        path = os.path.join(root, name)
        if os.path.isfile(path):
            return path
    return None


def save_avatar(data_dir, uid, data):
    # A PNG, JPEG, WebP or GIF, cropped square and stored as WebP; an animated GIF is stored as uploaded.
    # Raises editor.EditorError for a bad upload.
    encoded, ext = editor.portrait_bytes(data)
    root = root_for(data_dir, uid)
    for name in AVATAR_FILES:
        path = os.path.join(root, name)
        if os.path.isfile(path) and name != "avatar." + ext:
            os.remove(path)   # replacing a WebP with a GIF, or the other way round, leaves one file only
    _write_bytes(os.path.join(root, "avatar." + ext), encoded)


def remove_avatar(data_dir, uid):
    for name in AVATAR_FILES:
        path = os.path.join(root_for(data_dir, uid), name)
        if os.path.isfile(path):
            os.remove(path)


def background_path(data_dir, uid):
    for name in (BACKGROUND, BACKGROUND_JPEG):
        path = os.path.join(root_for(data_dir, uid), name)
        if os.path.isfile(path):
            return path
    return None


def background_video_path(data_dir, uid):
    path = os.path.join(root_for(data_dir, uid), BACKGROUND_VIDEO)
    return path if os.path.isfile(path) else None


def background_poster_path(data_dir, uid):
    path = os.path.join(root_for(data_dir, uid), BACKGROUND_POSTER)
    return path if os.path.isfile(path) else None


def save_background(data_dir, uid, data):
    # A PNG, JPEG or WebP, downsized as PNG; or an MP4 or MOV, transcoded to WebM with no audio and a poster
    # frame. The other kind is removed, so an account holds one background at a time either way.
    root = root_for(data_dir, uid)
    os.makedirs(root, exist_ok=True)
    if editor._video_kind(data) is not None:
        webm, still = editor.transcode_video(data)
        for name in (BACKGROUND, BACKGROUND_JPEG, BACKGROUND_POSTER):
            p = os.path.join(root, name)
            if os.path.isfile(p):
                os.remove(p)
        _write_bytes(os.path.join(root, BACKGROUND_VIDEO), webm)
        _write_bytes(os.path.join(root, BACKGROUND_POSTER), still)
        return
    encoded = editor.background_bytes(data)
    for name in (BACKGROUND, BACKGROUND_JPEG, BACKGROUND_VIDEO, BACKGROUND_POSTER):
        p = os.path.join(root, name)
        if os.path.isfile(p):
            os.remove(p)
    _write_bytes(os.path.join(root, BACKGROUND), encoded)


def remove_background(data_dir, uid):
    for name in (BACKGROUND, BACKGROUND_JPEG, BACKGROUND_VIDEO, BACKGROUND_POSTER):
        path = os.path.join(root_for(data_dir, uid), name)
        if os.path.isfile(path):
            os.remove(path)


def version(path):
    # File timestamp for cache busting; 0 when there is no picture.
    try:
        return int(os.path.getmtime(path)) if path else 0
    except OSError:
        return 0
