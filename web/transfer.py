# Character export and import as a zip. The archive holds the card and its files. An export can also carry
# one account's chat history and memories. Import never overwrites: an existing name is refused.
import datetime
import io
import json
import os
import re
import shutil
import zipfile

from web import editor, examples
from web.editor import EditorError

FORMAT = "miso-character"
VERSION = 1
MAX_ZIP = 60 * 1024 * 1024         # the uploaded archive
MAX_UNPACKED = 120 * 1024 * 1024   # the files inside it, summed before anything is extracted
CARD_FILES = ("character.md", "context.md", "examples.json", "profile.json", "creator.json", "palace.json",
              "sandbox.json", "icon.png", "icon.jpg", "icon.webp")
BG_MEMBER = re.compile(r"^backgrounds/[0-9a-f]{12}\.jpg$")   # one file per background in the archive
HISTORY_FILES = ("memory.json", "memory.md", "pinned.md", "summary.json", "episodes.json")
HISTORY_DIR = "history/"
NEUTRAL = "#8a8f98"
COLOUR_LINE = re.compile(r"(?im)^\s*[-*]?\s*colou?r\s*:\s*(#[0-9a-f]{6})\b")


def _state_dir(data_dir, uid, folder):
    return os.path.join(data_dir, "users", str(uid), folder)


def export_zip(root, data_dir, uid, name, include_history):
    # The character's files, plus this account's history and memories when asked for.
    path = editor.folder_of(root, name)
    if not os.path.isfile(os.path.join(path, "character.md")):
        raise EditorError("No such character.")
    members = [(f, os.path.join(path, f)) for f in CARD_FILES if os.path.isfile(os.path.join(path, f))]
    for bid in editor.background_ids(root, name):
        arc = "background.jpg" if bid == editor.LEGACY_ID else editor.BG_DIR + "/" + bid + ".jpg"
        members.append((arc, editor.background_path(root, name, bid)))
    has_history = False
    if include_history:
        state = _state_dir(data_dir, uid, os.path.basename(path))
        for f in HISTORY_FILES:
            if os.path.isfile(os.path.join(state, f)):
                members.append((HISTORY_DIR + f, os.path.join(state, f)))
                has_history = True
    manifest = {"format": FORMAT, "version": VERSION, "name": name,
                "creator": editor.creator_of(path), "includes_history": has_history,
                "exported": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
        for arc, src in members:
            z.write(src, arc)
    return buf.getvalue()


def _json_member(zf, info, label):
    try:
        return json.loads(zf.read(info).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise EditorError(f"{label} is not valid JSON.") from None


def _text_member(zf, info, label, limit=20000):
    try:
        text = zf.read(info).decode("utf-8")
    except UnicodeDecodeError:
        raise EditorError(f"{label} must be UTF-8 text.") from None
    if len(text) > limit:
        raise EditorError(f"{label} is over {limit:,} characters.")
    return text


def _is_background(n):
    return n == "background.jpg" or bool(BG_MEMBER.match(n))


def _check_members(zf):
    # Only known file names, no paths, and a total size limit, all checked before anything is extracted.
    allowed = set(CARD_FILES) | {"manifest.json"} | {HISTORY_DIR + f for f in HISTORY_FILES}
    found, total = {}, 0
    for info in zf.infolist():
        if info.is_dir():
            continue
        n = info.filename
        if n not in allowed and not _is_background(n):
            raise EditorError(f"Unexpected file in the archive: {n}")
        total += info.file_size
        if total > MAX_UNPACKED:
            raise EditorError("The files inside the archive are too large.")
        found[n] = info
    return found


def import_zip(root, data_dir, uid, username, data, admin=True):
    # Non-admins always become the creator, and cannot switch a sandbox on through an archive.
    # Creates a new character from an archive. Anything that fails after the folder is made removes it again.
    if not data:
        raise EditorError("Choose a file first.")
    if len(data) > MAX_ZIP:
        raise EditorError("Character archives can be up to 60 MB.")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise EditorError("That is not a zip archive.") from None
    with zf:
        members = _check_members(zf)
        if "manifest.json" not in members or "character.md" not in members:
            raise EditorError("The archive needs manifest.json and character.md.")
        manifest = _json_member(zf, members["manifest.json"], "manifest.json")
        if not isinstance(manifest, dict) or manifest.get("format") != FORMAT or manifest.get("version") != VERSION:
            raise EditorError("This is not a Miso Chat character archive, or its version is not supported.")
        name = str(manifest.get("name", ""))
        path = editor.folder_of(root, name)   # also checks the name
        if os.path.exists(path):
            raise EditorError("A character with that name already exists. Rename or remove it first.")

        card = _text_member(zf, members["character.md"], "character.md")
        if not card.strip():
            raise EditorError("character.md is empty.")
        colour = COLOUR_LINE.search(card)
        colour = colour.group(1) if colour else NEUTRAL
        context = _text_member(zf, members["context.md"], "context.md") if "context.md" in members else ""

        profile = None
        if "profile.json" in members:
            raw = _json_member(zf, members["profile.json"], "profile.json")
            if not isinstance(raw, dict):
                raise EditorError("profile.json must be an object.")
            profile = {"headline": raw.get("headline", ""), "info": raw.get("info", ""), "tags": raw.get("tags", [])}
        sandbox_on = None
        if admin and "sandbox.json" in members:
            flag = _json_member(zf, members["sandbox.json"], "sandbox.json")
            sandbox_on = bool(flag.get("enabled")) if isinstance(flag, dict) else None
        # Example messages travel with the card. A damaged list is refused rather than silently dropped.
        example_messages = None
        if "examples.json" in members:
            raw = _json_member(zf, members["examples.json"], "examples.json")
            raw = raw.get("examples") if isinstance(raw, dict) else raw
            try:
                example_messages = examples.validate(raw)
            except examples.ExamplesError as err:
                raise EditorError(f"examples.json is not usable: {err}") from None
        creator = username
        if admin and "creator.json" in members:
            c = _json_member(zf, members["creator.json"], "creator.json")
            if isinstance(c, dict) and isinstance(c.get("creator"), str) and c["creator"].strip():
                creator = c["creator"].strip()[:40]

        icon = next(((n, zf.read(members[n])) for n in ("icon.png", "icon.jpg", "icon.webp") if n in members), None)
        backgrounds = [zf.read(members[n]) for n in sorted(members) if _is_background(n)]

        history = {}
        for f in HISTORY_FILES:
            key = HISTORY_DIR + f
            if key not in members:
                continue
            if f == "memory.json" and not isinstance(_json_member(zf, members[key], "memory.json"), list):
                raise EditorError("memory.json must be a list of messages.")
            if f == "summary.json" and not isinstance(_json_member(zf, members[key], "summary.json"), dict):
                raise EditorError("summary.json must be an object.")
            history[f] = zf.read(members[key])

    created = False
    try:
        editor.write_card(root, name, card, context, colour, creating=True, sandbox_on=sandbox_on, profile=profile,
                          example_messages=example_messages)
        created = True
        editor.record_creator(path, creator)
        if icon is not None:
            editor.save_icon(root, name, icon[1])
        for data in backgrounds:
            editor.add_background(root, name, data)
    except Exception:
        if created:
            shutil.rmtree(path, ignore_errors=True)
        raise

    restored = False
    state = _state_dir(data_dir, uid, os.path.basename(path))
    if history and not os.path.exists(state):   # never overwrites the importer's own history
        os.makedirs(state)
        for f, raw in history.items():
            with open(os.path.join(state, f), "wb") as out:
                out.write(raw)
        restored = True
    return {"name": name, "creator": creator, "history_restored": restored}
