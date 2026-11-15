"""Emotion images for a character.

Emotion pictures are a character setting of their own: the character may use them whether or not agentic
mode is on. The model names one emotion at the start of each reply, and the chat shows the picture for
that emotion beside the speaker's name. When a picture is missing the chat shows a small emoticon instead.

Pictures live in the character folder as expressions/<emotion>.webp, one per emotion. The switches live
in agentic.json beside character.md, so they travel with the character folder.
"""
import json
import os

EMOTIONS = ("relaxed", "happy", "sad", "angry", "confused", "flustered", "steamy")
DEFAULT_EMOTION = "relaxed"
LABELS = {"relaxed": "Relaxed", "happy": "Happy", "sad": "Sad", "angry": "Angry",
          "confused": "Confused", "flustered": "Flustered", "steamy": "Steamy"}
# What the chat shows when there is no picture for the emotion. Text only, no emoji.
EMOTICONS = {"relaxed": "·ᴗ·", "happy": "＾▽＾", "sad": "T_T", "angry": "＞︿＜",
             "confused": "o_O", "flustered": "＞///＜", "steamy": "♨"}
FLAG_FILE = "agentic.json"
DIR_NAME = "expressions"
EXTS = (".webp", ".png", ".jpg", ".jpeg", ".gif")
IMAGE_MAX = 10 * 1024 * 1024   # bytes accepted for one upload, as for an icon


class ExpressionError(ValueError):
    pass


def clean_emotion(value):
    # One of the seven names, or "" when it is not one of them.
    name = str(value or "").strip().lower()
    return name if name in EMOTIONS else ""


def _flag_path(char_dir):
    return os.path.join(char_dir, FLAG_FILE)


def state(char_dir):
    # The two switches: agentic mode for tools, and emotion pictures for the chat face. They are set
    # separately; emotion pictures no longer need agentic mode. A missing file is off, like sandbox.json.
    try:
        with open(_flag_path(char_dir), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    agentic = bool(data.get("agentic")) if isinstance(data, dict) else False
    emotions = bool(data.get("emotions")) if isinstance(data, dict) else False
    return {"agentic": agentic, "emotions": emotions}


def set_state(char_dir, agentic, emotions):
    # The two switches are independent: emotion pictures can be on without agentic mode.
    data = {"agentic": bool(agentic), "emotions": bool(emotions)}
    tmp = _flag_path(char_dir) + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f)
    os.replace(tmp, _flag_path(char_dir))
    return state(char_dir)


def image_path(char_dir, emotion):
    # The picture for one emotion, or None. The first matching extension wins.
    if emotion not in EMOTIONS:
        return None
    folder = os.path.join(char_dir, DIR_NAME)
    for ext in EXTS:
        path = os.path.join(folder, emotion + ext)
        if os.path.isfile(path):
            return path
    return None


def images(char_dir):
    # The emotions that have a picture, in the usual order.
    return [name for name in EMOTIONS if image_path(char_dir, name) is not None]


def save_image(char_dir, emotion, data, shrink):
    # Store one emotion's picture, replacing any old one. shrink turns the upload into a square WebP and
    # raises the editor's own error for a file it cannot read.
    if emotion not in EMOTIONS:
        raise ExpressionError("Unknown emotion.")
    if not data:
        raise ExpressionError("Choose an image first.")
    if len(data) > IMAGE_MAX:
        raise ExpressionError("An emotion picture can be up to 10 MB.")
    payload, ext = shrink(data)
    folder = os.path.join(char_dir, DIR_NAME)
    os.makedirs(folder, exist_ok=True)
    for old in EXTS:
        path = os.path.join(folder, emotion + old)
        if os.path.isfile(path):
            os.remove(path)
    target = os.path.join(folder, emotion + "." + ext)
    tmp = target + ".part"
    with open(tmp, "wb") as f:
        f.write(payload)
    os.replace(tmp, target)
    return target


def remove_image(char_dir, emotion):
    if emotion not in EMOTIONS:
        raise ExpressionError("Unknown emotion.")
    removed = False
    for ext in EXTS:
        path = os.path.join(char_dir, DIR_NAME, emotion + ext)
        if os.path.isfile(path):
            os.remove(path)
            removed = True
    return removed


def view(char_dir):
    # What the pages read: both switches, which emotions have a picture, and the emoticon and label for each.
    flags = state(char_dir)
    return {"agentic": flags["agentic"], "emotions": flags["emotions"], "images": images(char_dir),
            "labels": LABELS, "emoticons": EMOTICONS, "default": DEFAULT_EMOTION,
            "emotions_list": list(EMOTIONS)}


def public(char_dir):
    # The smaller shape the chat page keeps for every character: what it needs to draw a message.
    flags = state(char_dir)
    if not flags["emotions"]:
        return None
    return {"images": images(char_dir), "emoticons": EMOTICONS, "default": DEFAULT_EMOTION}
