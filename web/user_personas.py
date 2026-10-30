# Personas one account makes for itself: a name, a colour, a free-text description and an optional picture.
# Only the owner sees or uses them. Each lives in the owner's folder as user.md (plus an icon) and is listed in
# the owner's persona list. Admin personas (shared, from personas.json) are unaffected.
import datetime
import json
import os
import re
import secrets
import shutil

INDEX = "personas.json"
FOLDER = "personas"
PREFIX = "usr-"
MAX_PERSONAS = 10
NAME_MAX = 40
DESC_MAX = 1500
ID_RE = re.compile(r"usr-[0-9a-f]{8}")
COLOUR_RE = re.compile(r"#[0-9a-fA-F]{6}")
DEFAULT_COLOUR = "#8b9bb4"


class PersonaError(ValueError):
    pass


def is_own_id(value):
    return isinstance(value, str) and bool(ID_RE.fullmatch(value))


def _index_path(data_dir, uid):
    return os.path.join(data_dir, "users", str(uid), INDEX)


def root_for(data_dir, uid):
    return os.path.join(data_dir, "users", str(uid), FOLDER)


def folder_for_persona(data_dir, uid, pid):
    return os.path.join(root_for(data_dir, uid), pid)


def _read(data_dir, uid):
    try:
        with open(_index_path(data_dir, uid), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    items = []
    for item in data.get("items", []) if isinstance(data, dict) else []:
        if not isinstance(item, dict) or not is_own_id(item.get("id")):
            continue
        items.append({
            "id": item["id"],
            "name": clean_name(item.get("name")) or "Persona",
            "colour": item.get("colour") if COLOUR_RE.fullmatch(str(item.get("colour", ""))) else DEFAULT_COLOUR,
            "description": clean_description(item.get("description")),
            "created": str(item.get("created") or ""),
        })
    return items


def _write(data_dir, uid, items):
    path = _index_path(data_dir, uid)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"items": items}, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def clean_name(name):
    return " ".join(str(name or "").split())[:NAME_MAX]


def clean_description(text):
    return str(text or "").replace("\r\n", "\n").strip()[:DESC_MAX]


def list_for(data_dir, uid):
    return _read(data_dir, uid)


def get(data_dir, uid, pid):
    for item in _read(data_dir, uid):
        if item["id"] == pid:
            return item
    return None


def _check(name, colour, description):
    name = clean_name(name)
    if not name:
        raise PersonaError("Give the persona a name.")
    if not COLOUR_RE.fullmatch(str(colour or "")):
        raise PersonaError("Pick a colour.")
    return name, str(colour).lower(), clean_description(description)


def write_card(folder, name, colour, description):
    # The engine reads user.md: the description first, then the Persona and Colour lines last, so a stray
    # "name:" or "colour:" inside the description can't change them.
    os.makedirs(folder, exist_ok=True)
    body = description.strip()
    text = (body + "\n\n" if body else "") + f"Persona: {name}\nColour: {colour}\n"
    tmp = os.path.join(folder, "user.md.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, os.path.join(folder, "user.md"))


def create(data_dir, uid, name, colour, description):
    items = _read(data_dir, uid)
    if len(items) >= MAX_PERSONAS:
        raise PersonaError(f"You can have up to {MAX_PERSONAS} personas.")
    name, colour, description = _check(name, colour, description)
    if any(i["name"].lower() == name.lower() for i in items):
        raise PersonaError("You already have a persona with that name.")
    pid = PREFIX + secrets.token_hex(4)
    write_card(folder_for_persona(data_dir, uid, pid), name, colour, description)
    item = {"id": pid, "name": name, "colour": colour, "description": description,
            "created": datetime.datetime.now().isoformat(timespec="seconds")}
    _write(data_dir, uid, items + [item])
    return item


def update(data_dir, uid, pid, name, colour, description):
    items = _read(data_dir, uid)
    target = next((i for i in items if i["id"] == pid), None)
    if target is None:
        raise PersonaError("No such persona.")
    name, colour, description = _check(name, colour, description)
    if any(i["name"].lower() == name.lower() and i["id"] != pid for i in items):
        raise PersonaError("You already have a persona with that name.")
    target.update(name=name, colour=colour, description=description)
    write_card(folder_for_persona(data_dir, uid, pid), name, colour, description)
    _write(data_dir, uid, items)
    return target


def delete(data_dir, uid, pid):
    items = _read(data_dir, uid)
    kept = [i for i in items if i["id"] != pid]
    if len(kept) == len(items):
        raise PersonaError("No such persona.")
    _write(data_dir, uid, kept)
    shutil.rmtree(folder_for_persona(data_dir, uid, pid), ignore_errors=True)
