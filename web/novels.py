# Novels: one account's stories told by a narrator. A novel has a cast of solo characters, one world (or none, or the
# account default), and its own chat, memory and tensions in the owner's folder. Only the owner sees or edits them.
# A novel's chat is written as prose: the narrator sets the scene and voices the cast in the third person. Novels are
# kept in their own index, apart from group chats, so the group sidebar and its counts do not change.
import datetime
import os
import re
import secrets
import shutil

from web import user_index

INDEX = "novels.json"
PREFIX = "nvl-"
ID_RE = re.compile(r"nvl-[0-9a-f]{8}")
MAX_NOVELS = 20
NAME_MAX = 60
MIN_CAST = 1
MAX_CAST = 8
NO_WORLD = "none"      # the novel is explicitly set to have no world (see worlds.NO_WORLD)


class NovelError(ValueError):
    pass


# The novel folder's ID prefix tells a novel from a character or a group, wherever a name is read.
def is_novel(name):
    return isinstance(name, str) and name.startswith(PREFIX)


def state_root(data_dir, uid, nid):
    return user_index.item_path(data_dir, uid, nid)


# Reads an account's novels. A damaged index means no novels, not a broken page.
def _read(data_dir, uid):
    items = []
    for item in user_index.read_items(data_dir, uid, INDEX):
        if not isinstance(item.get("id"), str) or not ID_RE.fullmatch(item["id"]):
            continue
        raw_members = item.get("members")
        members = [m for m in raw_members if isinstance(m, str)] if isinstance(raw_members, list) else []
        world = item.get("world") if isinstance(item.get("world"), str) else None
        items.append({"id": item["id"], "name": clean_name(item.get("name")) or "Novel", "members": members,
                      "world": world, "created": str(item.get("created") or "")})
    return items


def _write(data_dir, uid, items):
    user_index.write_items(data_dir, uid, INDEX, items)


def clean_name(name):
    return user_index.clean_name(name, NAME_MAX)


def list_for(data_dir, uid):
    return _read(data_dir, uid)


def get(data_dir, uid, nid):
    return user_index.find_by_id(_read(data_dir, uid), nid)


# Checks a novel's name, cast and world. The cast must be solo characters the account can see; the world must be one of
# the account's worlds or None.
def _check(name, members, solo, world_ids):
    name = clean_name(name)
    if not name:
        raise NovelError("Give the novel a name.")
    if not isinstance(members, list) or not all(isinstance(m, str) for m in members):
        raise NovelError("The cast must be a list of characters.")
    members = list(dict.fromkeys(members))
    if not MIN_CAST <= len(members) <= MAX_CAST:
        raise NovelError(f"A novel needs {MIN_CAST} to {MAX_CAST} characters.")
    unknown = [m for m in members if m not in solo]
    if unknown:
        raise NovelError("Unknown character: " + ", ".join(unknown[:3]))
    return name, members


def _check_world(world, world_ids):
    if world is None or world == "":
        return None
    if world == NO_WORLD:
        return NO_WORLD
    if world not in world_ids:
        raise NovelError("That world no longer exists.")
    return world


def create(data_dir, uid, name, members, world, solo, world_ids, now_iso=None):
    items = _read(data_dir, uid)
    if len(items) >= MAX_NOVELS:
        raise NovelError(f"An account can have up to {MAX_NOVELS} novels. Delete one first.")
    name, members = _check(name, members, solo, world_ids)
    world = _check_world(world, world_ids)
    nid = PREFIX + secrets.token_hex(4)
    while os.path.exists(state_root(data_dir, uid, nid)):
        nid = PREFIX + secrets.token_hex(4)
    item = {"id": nid, "name": name, "members": members, "world": world,
            "created": now_iso or datetime.datetime.now().isoformat(timespec="seconds")}
    items.append(item)
    _write(data_dir, uid, items)
    os.makedirs(state_root(data_dir, uid, nid), exist_ok=True)
    return item


def update(data_dir, uid, nid, name, members, world, solo, world_ids):
    items = _read(data_dir, uid)
    for item in items:
        if item["id"] == nid:
            name, members = _check(name, members, solo, world_ids)
            item["name"], item["members"] = name, members
            item["world"] = _check_world(world, world_ids)
            _write(data_dir, uid, items)
            return item
    raise NovelError("That novel no longer exists.")


# Sets the world a novel uses: an account world ID, NO_WORLD, or None for the account default.
def set_world(data_dir, uid, nid, world, world_ids):
    items = _read(data_dir, uid)
    for item in items:
        if item["id"] == nid:
            item["world"] = _check_world(world, world_ids)
            _write(data_dir, uid, items)
            return item
    raise NovelError("That novel no longer exists.")


def delete(data_dir, uid, nid):
    items = _read(data_dir, uid)
    if not any(i["id"] == nid for i in items):
        raise NovelError("That novel no longer exists.")
    _write(data_dir, uid, [i for i in items if i["id"] != nid])
    shutil.rmtree(state_root(data_dir, uid, nid), ignore_errors=True)


# Clears a world from every novel that used it. Called when the world is deleted.
def forget_world(data_dir, uid, wid):
    items = _read(data_dir, uid)
    changed = False
    for item in items:
        if item["world"] == wid:
            item["world"] = None
            changed = True
    if changed:
        _write(data_dir, uid, items)
