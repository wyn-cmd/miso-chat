# Character names <-> folder names.
#
# A display name can hold any character a card needs (colons, brackets, #, %, ?, ...). A folder on
# Windows can't hold some of those, so the forbidden characters are percent-encoded in the folder name:
#   "Dr. Fox: Night Form"  ->  folder "Dr. Fox%3A Night Form"
# The folder name is the stable ID used on disk, in group_config.json, personas.json and inside the
# web API. Ordinary names (letters, digits, spaces, _ - . and non-ASCII letters) map to themselves,
# so existing folders keep their names.
import os
import re

_FORBIDDEN = '<>:"/\\|?*%'
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
_ESCAPE_RE = re.compile(r"%([0-9A-Fa-f]{2})")


def _escape(ch):
    return "%%%02X" % ord(ch)


def folder_for(name):
    # Display name -> folder name. Stable: folder_for(display_for(f)) == f for every folder made here.
    name = str(name)
    chars = []
    for ch in name:
        if ch in _FORBIDDEN or ord(ch) < 32 or ord(ch) == 127:
            chars.append(_escape(ch))
        else:
            chars.append(ch)
    # Windows drops trailing dots and spaces, so encode them; a leading dot is rejected by the editors.
    i = len(chars)
    while i > 0 and chars[i - 1] in (".", " "):
        i -= 1
        chars[i] = _escape(chars[i])
    if chars and chars[0] == ".":
        chars[0] = _escape(".")
    folder = "".join(chars)
    stem = folder.split(".", 1)[0].upper()
    if stem in _RESERVED:
        # CON, NUL, COM1, ... are device names on Windows even with an extension; encode the first letter.
        folder = _escape(folder[0]) + folder[1:]
    return folder


def display_for(folder):
    # Folder name -> display name. Every '%' in a folder name is an escape, so this is an exact inverse.
    return _ESCAPE_RE.sub(lambda m: chr(int(m.group(1), 16)), str(folder))


def resolve_folder(arg, root="."):
    # A character or group argument from the command line: an existing folder name, or a display name.
    if os.path.isdir(os.path.join(root, arg)):
        return arg
    encoded = folder_for(arg)
    return encoded


def check_display(name):
    # Validate a display name typed by a user. Returns the stripped name, or raises ValueError.
    if not isinstance(name, str):
        raise ValueError("Names are text.")
    name = name.strip()
    if not 1 <= len(name) <= 40:
        raise ValueError("Names are 1 to 40 characters.")
    if any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise ValueError("Names can't contain control characters.")
    if name in (".", "..") or name.startswith((".", "__")):
        raise ValueError("Names can't start with a dot or two underscores.")
    return name
