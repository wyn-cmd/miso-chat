# Character editor for the web app: create, read and update character folders, and manage icons.
#
# Admin only. A character is a folder under the character root with character.md (persona, with a
# 'Colour:' line), optional context.md (memories, locations, situations) and optional icon.png.
# Names are restricted to one folder level so nothing can be written outside the character root.
import io
import json
import os
import re
import secrets

from PIL import Image, ImageOps

import sandbox
from charname import check_display, folder_for
from web import profiles, examples

COLOUR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
UPLOAD_MAX = 10 * 1024 * 1024         # largest file an admin may upload
ICON_OUT_MAX = 512 * 1024             # the stored icon stays under this
ICON_SIDES = (512, 384, 320, 256, 192, 128, 96)   # tried largest first; the first that fits is kept
MAX_SOURCE_PIXELS = 40_000_000        # refuse huge images before decoding them
EXAMPLE_CARD = """Character Persona: Example Fox
Colour: #e8a33d

- Species: A lean fox anthro with a crimson scarf and a brass pocket watch.
- Personality: Curious and warm, teases gently, hates being rushed.
- Appearance: Amber fur, cream muzzle, green eyes, scarf always tied.
- Setting: A rainy harbour town; the lighthouse keeper is an old friend.
- Speech: Short sentences, fond nicknames, the occasional *tail flick*.
"""
# The example messages that go with the sample card, so the editor's example section shows what it is for.
EXAMPLE_MESSAGES = [
    {"user": "You're late again, fox.",
     "character": "*one ear tips toward you, unbothered* The tide was late first. I only followed it."},
    {"user": "What did you find down at the harbour?",
     "character": "A brass cog with no machine left to want it. *turns it over in her palm* Yours, if you like."},
]


class EditorError(ValueError):
    pass


def valid_name(name):
    # A display name as the admin types it. The folder name is derived from it (charname.folder_for).
    try:
        return check_display(name)
    except ValueError as err:
        raise EditorError(str(err)) from None


def folder_of(root, name):
    name = valid_name(name)
    path = os.path.join(root, folder_for(name))
    if os.path.dirname(os.path.realpath(path)) != os.path.realpath(root):
        raise EditorError("That name points outside the character folder.")
    return path


def read_card(root, name):
    path = folder_of(root, name)
    if not os.path.isfile(os.path.join(path, "character.md")):
        raise EditorError("No such character.")
    with open(os.path.join(path, "character.md"), encoding="utf-8", errors="replace") as f:
        card = f.read()
    context = ""
    if os.path.isfile(os.path.join(path, "context.md")):
        with open(os.path.join(path, "context.md"), encoding="utf-8", errors="replace") as f:
            context = f.read()
    m = re.search(r"^\s*[-*]?\s*colou?r\s*:\s*(.+)$", card, re.I | re.M)
    colour = m.group(1).split("/")[0].strip() if m else ""
    return {"name": name, "card": card, "context": context, "colour": colour,
            "has_icon": icon_path(root, name) is not None, "backgrounds": background_ids(root, name), "sandbox": sandbox.is_enabled(path),
            "profile": profiles.read_profile(path), "examples": examples.load(path)}


def write_card(root, name, card, context, colour_hex, creating, sandbox_on=None, profile=None, example_messages=None):
    # profile is the card's headline, info and tags. Blank headline and info leave the saved profile
    # alone, so the background writer can still fill it in later. example_messages is None when the caller does not
    # manage them, which leaves the saved list as it is; a list replaces it (an empty list removes the file).
    path = folder_of(root, name)
    if creating and os.path.exists(path):
        raise EditorError("A character with that name already exists.")
    if not creating and not os.path.isfile(os.path.join(path, "character.md")):
        raise EditorError("No such character.")
    if not isinstance(card, str) or not card.strip():
        raise EditorError("The character card can't be empty.")
    if len(card) > 20000 or len(context or "") > 20000:
        raise EditorError("Keep the card and context under 20,000 characters each.")
    new_profile = _checked_profile(profile)   # checked before anything is written
    # Checked before anything is written as well, so a bad example never leaves a half-saved card.
    try:
        new_examples = None if example_messages is None else examples.validate(example_messages)
    except examples.ExamplesError as err:
        raise EditorError(str(err)) from None
    colour_hex = (colour_hex or "").strip()
    if COLOUR_RE.match(colour_hex):
        card = _set_colour_line(card, colour_hex.lower())
    elif creating:
        raise EditorError("Colour must be a hex code such as #c0392b.")
    # on an existing character, a colour name such as 'Crimson' is kept as it is in the card
    os.makedirs(path, exist_ok=True)
    _atomic_write(os.path.join(path, "character.md"), card)
    if sandbox_on is not None:      # None leaves the flag as it is
        sandbox.set_enabled(path, sandbox_on)
    context_path = os.path.join(path, "context.md")
    if (context or "").strip():
        _atomic_write(context_path, context.strip() + "\n")
    elif os.path.exists(context_path):
        os.remove(context_path)
    if new_profile is not None:
        profiles.save_profile(path, new_profile, auto=False)
    if new_examples is not None:
        examples.save(path, new_examples)
    return read_card(root, name)


def _checked_profile(profile):
    # None when the admin left the profile blank; otherwise the cleaned profile or an EditorError.
    if not isinstance(profile, dict):
        return None
    headline = str(profile.get("headline") or "")
    info = str(profile.get("info") or "")
    if not headline.strip() and not info.strip():
        return None
    try:
        return profiles.clean_profile(headline, info, profile.get("tags"))
    except profiles.ProfileError as err:
        raise EditorError(str(err)) from None


def _set_colour_line(card, colour_hex):
    # Keep one Colour: line in the card, set to the hex the bubbles use.
    lines = card.replace("\r\n", "\n").split("\n")
    for i, line in enumerate(lines):
        if re.match(r"^\s*[-*]?\s*colou?r\s*:", line, re.I):
            lines[i] = "Colour: " + colour_hex
            return "\n".join(lines)
    return "Colour: " + colour_hex + "\n" + "\n".join(lines)


def _atomic_write(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


CREATOR_FILE = "creator.json"
DEFAULT_CREATOR = "admin"   # folders from before creator tracking have no file


def creator_of(path):
    # Username that made the character. Missing or unreadable means the default.
    try:
        with open(os.path.join(path, CREATOR_FILE), encoding="utf-8") as f:
            name = json.load(f).get("creator")
    except (OSError, ValueError, AttributeError):
        return DEFAULT_CREATOR
    return name if isinstance(name, str) and name.strip() else DEFAULT_CREATOR


def record_creator(path, username):
    # Written once at creation, so later edits never change who made the character.
    target = os.path.join(path, CREATOR_FILE)
    if os.path.exists(target):
        return
    _atomic_write(target, json.dumps({"creator": username}) + "\n")


BG_LEGACY = "background.jpg"         # the single background from before several were allowed; still served
BG_DIR = "backgrounds"               # one JPEG per background, named by its id
BG_MAX_SIDE = 1280                   # chat and card backgrounds are downsized to this
MAX_BACKGROUNDS = 10                 # per character
LEGACY_ID = "legacy"
BG_ID_RE = re.compile(r"^[0-9a-f]{12}$")


def _encode_background(data):
    # Checks an upload and returns it downsized as JPEG, in its original colours.
    if not data:
        raise EditorError("Choose an image first.")
    if len(data) > UPLOAD_MAX:
        raise EditorError("Background uploads can be up to 10 MB.")
    try:
        img = Image.open(io.BytesIO(data))
        if img.format not in ("PNG", "JPEG", "WEBP"):
            raise EditorError("Background must be a PNG, JPEG or WebP image.")
        if img.width * img.height > MAX_SOURCE_PIXELS:
            raise EditorError("That image is too large. Use one under 40 megapixels.")
        img = ImageOps.exif_transpose(img)
        img.load()
    except EditorError:
        raise
    except (OSError, ValueError, Image.DecompressionBombError) as err:
        raise EditorError("Could not read that image. Use a PNG, JPEG or WebP.") from err
    img = img.convert("RGB")
    img.thumbnail((BG_MAX_SIDE, BG_MAX_SIDE), Image.LANCZOS)   # never enlarges
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=82, optimize=True)
    return buf.getvalue()


def background_bytes(data):
    # Checked upload, downsized as JPEG. Also used for account profile backgrounds.
    return _encode_background(data)


def background_ids(root, name):
    # The legacy single file first, then the rest in name order. Empty when there are none.
    try:
        folder = folder_of(root, name)
    except EditorError:
        return []
    ids = []
    if os.path.isfile(os.path.join(folder, BG_LEGACY)):
        ids.append(LEGACY_ID)
    bdir = os.path.join(folder, BG_DIR)
    if os.path.isdir(bdir):
        for f in sorted(os.listdir(bdir)):
            stem, ext = os.path.splitext(f)
            if ext == ".jpg" and BG_ID_RE.match(stem) and os.path.isfile(os.path.join(bdir, f)):
                ids.append(stem)
    return ids


def _bg_file(root, name, bid):
    # The file for one id, or None. Ids are checked before they touch the disk.
    if bid != LEGACY_ID and not BG_ID_RE.match(bid or ""):
        return None
    try:
        folder = folder_of(root, name)
    except EditorError:
        return None
    p = os.path.join(folder, BG_LEGACY) if bid == LEGACY_ID else os.path.join(folder, BG_DIR, bid + ".jpg")
    return p if os.path.isfile(p) else None


def background_path(root, name, bid=None):
    # With no id, the first background. Cards and chats ask for a specific one.
    if bid is None:
        ids = background_ids(root, name)
        if not ids:
            return None
        bid = ids[0]
    return _bg_file(root, name, bid)


def add_background(root, name, data):
    # Adds one background and returns its id. Refuses past MAX_BACKGROUNDS.
    path = folder_of(root, name)
    if not os.path.isdir(path):
        raise EditorError("No such character.")
    encoded = _encode_background(data)
    if len(background_ids(root, name)) >= MAX_BACKGROUNDS:
        raise EditorError(f"A character can have up to {MAX_BACKGROUNDS} backgrounds. Remove one first.")
    bdir = os.path.join(path, BG_DIR)
    os.makedirs(bdir, exist_ok=True)
    bid = secrets.token_hex(6)
    while os.path.exists(os.path.join(bdir, bid + ".jpg")):
        bid = secrets.token_hex(6)
    tmp = os.path.join(bdir, bid + ".tmp")
    with open(tmp, "wb") as f:
        f.write(encoded)
    os.replace(tmp, os.path.join(bdir, bid + ".jpg"))
    return bid


def remove_background(root, name, bid):
    p = _bg_file(root, name, bid)
    if p is None:
        raise EditorError("No such background.")
    os.remove(p)


def _shrink_icon(data):
    # Crop to a centred square, then keep the highest resolution that stays under ICON_OUT_MAX.
    # Returns (bytes, extension): always WebP, with transparency kept when the source has it. Never upscales.
    try:
        img = Image.open(io.BytesIO(data))
        if img.format not in ("PNG", "JPEG", "WEBP"):
            raise EditorError("Icon must be a PNG, JPEG or WebP image.")
        if img.width * img.height > MAX_SOURCE_PIXELS:
            raise EditorError("That image is too large. Use one under 40 megapixels.")
        img = ImageOps.exif_transpose(img)
        img.load()
    except EditorError:
        raise
    except (OSError, ValueError, Image.DecompressionBombError) as err:
        raise EditorError("Could not read that image. Use a PNG, JPEG or WebP.") from err
    alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
    img = img.convert("RGBA" if alpha else "RGB")
    side = min(img.size)
    left, top = (img.width - side) // 2, (img.height - side) // 2
    img = img.crop((left, top, left + side, top + side))
    for size in sorted({min(s, side) for s in ICON_SIDES}, reverse=True):
        small = img.resize((size, size), Image.LANCZOS)
        for quality in (90, 80, 70):
            buf = io.BytesIO()
            small.save(buf, "WEBP", quality=quality, method=6)
            if len(buf.getvalue()) < ICON_OUT_MAX:
                return buf.getvalue(), "webp"
    raise EditorError("That picture is too detailed to fit under 512 KB. Try a simpler image.")


def portrait_bytes(data):
    # Checked upload, cropped to a centred square and stored as WebP. Returns (bytes, extension).
    # Account profile pictures use this too.
    return _shrink_icon(data)


def save_icon(root, name, data):
    # Store a character's icon, replacing any old one. Accepts a PNG or JPEG up to 10 MB; it is cropped to a
    # centred square and its resolution lowered only as far as needed to stay under 512 KB. Returns the file name.
    path = folder_of(root, name)
    if not os.path.isdir(path):
        raise EditorError("No such character.")
    if not data:
        raise EditorError("Choose an image first.")
    if len(data) > UPLOAD_MAX:
        raise EditorError("Icon uploads can be up to 10 MB.")
    out, ext = _shrink_icon(data)
    for old in ("icon.png", "icon.jpg", "icon.webp"):
        if os.path.exists(os.path.join(path, old)):
            os.remove(os.path.join(path, old))
    target = "icon." + ext
    with open(os.path.join(path, target), "wb") as f:
        f.write(out)
    return target


def icon_path(root, name):
    path = folder_of(root, name)
    for fname in ("icon.png", "icon.jpg", "icon.webp"):
        p = os.path.join(path, fname)
        if os.path.isfile(p):
            return p
    return None


def example_card():
    return EXAMPLE_CARD


def example_messages():
    return [dict(pair) for pair in EXAMPLE_MESSAGES]
