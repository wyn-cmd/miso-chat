# Private group chats: one account's groups of solo characters. Only the owner sees or edits them.
# Member characters stay shared. Each group's chat, memory and notes live in the owner's folder.
import datetime
import os
import re
import secrets
import shutil

from web import user_index

INDEX = "private_groups.json"
PREFIX = "grp-"
MAX_GROUPS = 20
NAME_MAX = 40
MIN_MEMBERS = 2
MAX_MEMBERS = 8
NOTE_MAX = 500
NOTES_FILE = "notes.json"
ID_RE = re.compile(r"grp-[0-9a-f]{8}")


class GroupError(ValueError):
    pass


def state_root(data_dir, uid, gid):
    return user_index.item_path(data_dir, uid, gid)


def _read(data_dir, uid):
    items = []
    for item in user_index.read_items(data_dir, uid, INDEX):
        if not isinstance(item.get("id"), str) or not ID_RE.fullmatch(item["id"]):
            continue
        raw_members = item.get("members")
        members = [m for m in raw_members if isinstance(m, str)] if isinstance(raw_members, list) else []
        items.append({"id": item["id"], "name": clean_name(item.get("name")) or "Group",
                      "members": members, "created": str(item.get("created") or "")})
    return items


def _write(data_dir, uid, items):
    user_index.write_items(data_dir, uid, INDEX, items)


def clean_name(name):
    return user_index.clean_name(name, NAME_MAX)


def list_for(data_dir, uid):
    return _read(data_dir, uid)


def get(data_dir, uid, gid):
    return user_index.find_by_id(_read(data_dir, uid), gid)


def find_by_key(data_dir, uid, key, key_of):
    # Matches a display name to one of the account's groups, using the same key as the character folders.
    for item in _read(data_dir, uid):
        if key_of(item["name"]) == key:
            return item
    return None


def _check(name, members, solo, taken, key_of):
    name = clean_name(name)
    if not name:
        raise GroupError("Give the group a name.")
    if key_of(name) in taken:
        raise GroupError("A character or another group already uses that name.")
    if not isinstance(members, list) or not all(isinstance(m, str) for m in members):
        raise GroupError("Members must be a list of characters.")
    members = list(dict.fromkeys(members))
    if not MIN_MEMBERS <= len(members) <= MAX_MEMBERS:
        raise GroupError(f"A group needs {MIN_MEMBERS} to {MAX_MEMBERS} characters.")
    unknown = [m for m in members if m not in solo]
    if unknown:
        raise GroupError("Unknown character: " + ", ".join(unknown[:3]))
    return name, members


def create(data_dir, uid, name, members, solo, taken, key_of):
    items = _read(data_dir, uid)
    if len(items) >= MAX_GROUPS:
        raise GroupError(f"An account can have up to {MAX_GROUPS} group chats. Delete one first.")
    name, members = _check(name, members, solo, taken, key_of)
    gid = PREFIX + secrets.token_hex(4)
    while os.path.exists(state_root(data_dir, uid, gid)):
        gid = PREFIX + secrets.token_hex(4)
    item = {"id": gid, "name": name, "members": members,
            "created": datetime.datetime.now().isoformat(timespec="seconds")}
    items.append(item)
    _write(data_dir, uid, items)
    os.makedirs(state_root(data_dir, uid, gid), exist_ok=True)
    return item


def update(data_dir, uid, gid, name, members, solo, taken, key_of):
    items = _read(data_dir, uid)
    for item in items:
        if item["id"] == gid:
            name, members = _check(name, members, solo, taken, key_of)
            item["name"], item["members"] = name, members
            _write(data_dir, uid, items)
            return item
    raise GroupError("That group no longer exists.")


def delete(data_dir, uid, gid):
    items = _read(data_dir, uid)
    if not any(i["id"] == gid for i in items):
        raise GroupError("That group no longer exists.")
    _write(data_dir, uid, [i for i in items if i["id"] != gid])
    shutil.rmtree(state_root(data_dir, uid, gid), ignore_errors=True)


def get_notes(data_dir, uid, gid):
    # One note per member, kept in the group's own folder so every branch of the group shares them.
    data = user_index.read_json(os.path.join(state_root(data_dir, uid, gid), NOTES_FILE))
    if not isinstance(data, dict):
        return {}
    return {k: str(v)[:NOTE_MAX] for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}


def set_notes(data_dir, uid, gid, notes, members):
    # Notes for members that are no longer in the group are dropped.
    if not isinstance(notes, dict):
        raise GroupError("Notes must be a list of member notes.")
    clean = {}
    for member in members:
        text = str(notes.get(member, "") or "").strip()[:NOTE_MAX]
        if text:
            clean[member] = text
    root = state_root(data_dir, uid, gid)
    user_index.write_json(os.path.join(root, NOTES_FILE), clean)
    return clean
