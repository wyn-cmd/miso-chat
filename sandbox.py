# A private folder per character and account that the model can use through a few file tools.
#
# The model sees a virtual root '/'. Every path it sends is checked here before anything touches the disk:
# it must be relative, use only plain names, contain no links or junctions along the way, and resolve inside
# the sandbox root. Errors name virtual paths only, so the real location never reaches the model.
import json
import os
import shutil
import uuid

FLAG_FILE = "sandbox.json"          # in the character folder: {"enabled": true|false}; missing means off
DIR_NAME = "sandbox"                # per account and character, under the state folder (next to memory.json)
MAX_FILE_BYTES = 64 * 1024          # largest single file the model may write
MAX_TOTAL_BYTES = 5 * 1024 * 1024   # whole sandbox
MAX_FILES = 300
MAX_DEPTH = 8
MAX_NAME = 80
MAX_PATH = 400
READ_CHARS = 20000                  # longest text a read returns
LIST_MAX = 200
BAD_NAME_CHARS = set('<>:"|?*\\')
RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}


class SandboxError(ValueError):
    pass


def is_enabled(char_dir):
    path = os.path.join(char_dir, FLAG_FILE)
    if not os.path.isfile(path):
        return False
    try:
        with open(path, encoding="utf-8") as f:
            return bool(json.load(f).get("enabled", False))
    except (OSError, ValueError, AttributeError):
        return False


def set_enabled(char_dir, enabled):
    path = os.path.join(char_dir, FLAG_FILE)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"enabled": bool(enabled)}, f)
    os.replace(tmp, path)


def root_for(state_dir):
    return os.path.join(state_dir, DIR_NAME)


def _is_link(path):
    if os.path.islink(path):
        return True
    isjunction = getattr(os.path, "isjunction", None)
    return bool(isjunction and isjunction(path))


def _split(vpath):
    if not isinstance(vpath, str):
        raise SandboxError("Path must be text.")
    if len(vpath) > MAX_PATH:
        raise SandboxError("That path is too long.")
    if any(ord(c) < 32 for c in vpath) or "\\" in vpath or ":" in vpath:
        raise SandboxError("Use plain names separated by '/'.")
    parts = [p for p in vpath.split("/") if p not in ("", ".")]
    if ".." in parts:
        raise SandboxError("'..' is not allowed. Paths start at '/'.")
    if len(parts) > MAX_DEPTH:
        raise SandboxError("That folder is nested too deeply.")
    for p in parts:
        if len(p) > MAX_NAME or p.endswith((" ", ".")) or any(c in BAD_NAME_CHARS for c in p):
            raise SandboxError(f"'{p}' is not a valid name.")
        if p.split(".")[0].upper() in RESERVED:
            raise SandboxError(f"'{p}' is a reserved name.")
    return parts


def resolve(root, vpath):
    # Real path for a virtual path, or raise. Walks each component so links are caught before they are followed.
    parts = _split(vpath)
    os.makedirs(root, exist_ok=True)
    real_root = os.path.realpath(root)
    cur = real_root
    for p in parts:
        cur = os.path.join(cur, p)
        if os.path.lexists(cur) and _is_link(cur):
            raise SandboxError("Links are not allowed in the sandbox.")
    resolved = os.path.realpath(cur)
    if resolved != real_root and not resolved.startswith(real_root + os.sep):
        raise SandboxError("That path is outside the sandbox.")
    return resolved, "/" + "/".join(parts)


def usage(root):
    files = total = 0
    if os.path.isdir(root):
        for dp, dn, fn in os.walk(root):
            for f in fn:
                files += 1
                try:
                    total += os.path.getsize(os.path.join(dp, f))
                except OSError:
                    pass
    return files, total


def _check_room(root, add_bytes, add_files):
    files, total = usage(root)
    if files + add_files > MAX_FILES:
        raise SandboxError(f"The sandbox holds at most {MAX_FILES} files. Delete some first.")
    if total + add_bytes > MAX_TOTAL_BYTES:
        raise SandboxError("The sandbox is full. Delete some files first.")


def list_dir(root, vpath="/"):
    real, v = resolve(root, vpath)
    if not os.path.isdir(real):
        raise SandboxError(f"{v} is not a folder.")
    names = sorted(os.listdir(real), key=str.lower)
    out = []
    for name in names:
        full = os.path.join(real, name)
        if _is_link(full):
            continue
        isdir = os.path.isdir(full)
        out.append({"name": name, "type": "folder" if isdir else "file",
                    "size": 0 if isdir else os.path.getsize(full)})
        if len(out) >= LIST_MAX:
            break
    return {"path": v, "entries": out, "truncated": len(names) > LIST_MAX}


def read_file(root, vpath):
    real, v = resolve(root, vpath)
    if not os.path.isfile(real):
        raise SandboxError(f"{v} is not a file.")
    if os.path.getsize(real) > MAX_FILE_BYTES:
        raise SandboxError(f"{v} is larger than {MAX_FILE_BYTES // 1024} KB.")
    with open(real, encoding="utf-8", errors="replace") as f:
        text = f.read()
    return {"path": v, "text": text[:READ_CHARS], "truncated": len(text) > READ_CHARS}


def write_file(root, vpath, content):
    if not isinstance(content, str):
        raise SandboxError("Content must be text.")
    data = content.encode("utf-8")
    if len(data) > MAX_FILE_BYTES:
        raise SandboxError(f"Files can be at most {MAX_FILE_BYTES // 1024} KB.")
    real, v = resolve(root, vpath)
    if os.path.isdir(real):
        raise SandboxError(f"{v} is a folder.")
    existed = os.path.isfile(real)
    old = os.path.getsize(real) if existed else 0
    _check_room(root, len(data) - old, 0 if existed else 1)
    parent = os.path.dirname(real)
    os.makedirs(parent, exist_ok=True)
    tmp = os.path.join(parent, f".{uuid.uuid4().hex}.tmp")
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, real)
    return {"path": v, "bytes": len(data), "created": not existed}


def make_dir(root, vpath):
    real, v = resolve(root, vpath)
    if os.path.lexists(real):
        raise SandboxError(f"{v} already exists.")
    os.makedirs(real)
    return {"path": v, "created": True}


def move(root, src, dst):
    s_real, s_v = resolve(root, src)
    if s_v == "/":
        raise SandboxError("The root folder cannot be moved.")
    if not os.path.lexists(s_real):
        raise SandboxError(f"{s_v} does not exist.")
    d_real, d_v = resolve(root, dst)
    if d_real.startswith(s_real + os.sep):
        raise SandboxError("A folder cannot be moved into itself.")
    if os.path.lexists(d_real):
        raise SandboxError(f"{d_v} already exists.")
    os.makedirs(os.path.dirname(d_real), exist_ok=True)
    os.replace(s_real, d_real)
    return {"from": s_v, "to": d_v}


def copy(root, src, dst):
    s_real, s_v = resolve(root, src)
    if s_v == "/":
        raise SandboxError("The root folder cannot be copied.")
    if not os.path.exists(s_real):
        raise SandboxError(f"{s_v} does not exist.")
    d_real, d_v = resolve(root, dst)
    if os.path.lexists(d_real):
        raise SandboxError(f"{d_v} already exists.")
    if os.path.isdir(s_real):
        if d_real.startswith(s_real + os.sep):
            raise SandboxError("A folder cannot be copied into itself.")
        add_files = add_bytes = 0
        for dp, dn, fn in os.walk(s_real):
            for f in fn:
                add_files += 1
                add_bytes += os.path.getsize(os.path.join(dp, f))
        _check_room(root, add_bytes, add_files)
        shutil.copytree(s_real, d_real, symlinks=True)
    else:
        size = os.path.getsize(s_real)
        if size > MAX_FILE_BYTES:
            raise SandboxError(f"{s_v} is larger than {MAX_FILE_BYTES // 1024} KB.")
        _check_room(root, size, 1)
        os.makedirs(os.path.dirname(d_real), exist_ok=True)
        shutil.copy2(s_real, d_real)
    return {"from": s_v, "to": d_v}


def delete(root, vpath):
    real, v = resolve(root, vpath)
    if v == "/":
        raise SandboxError("The root folder cannot be deleted.")
    if not os.path.lexists(real):
        raise SandboxError(f"{v} does not exist.")
    if os.path.isdir(real):
        shutil.rmtree(real)
    else:
        os.remove(real)
    return {"deleted": v}


# Tool declarations shown to the model. rp_core turns these into Gemini function declarations.
TOOLS = [
    {"name": "list_folder", "description": "List the files and folders in a sandbox folder. Use '/' for the top.",
     "params": {"path": ("string", "Folder path such as '/' or '/notes'.")}, "required": ["path"]},
    {"name": "read_file", "description": "Read a text file in the sandbox.",
     "params": {"path": ("string", "File path such as '/notes/day1.md'.")}, "required": ["path"]},
    {"name": "write_file", "description": "Create or replace a text file in the sandbox. Folders in the path are created.",
     "params": {"path": ("string", "File path such as '/notes/day1.md'."),
                "content": ("string", "The full text to save.")}, "required": ["path", "content"]},
    {"name": "make_folder", "description": "Create a folder in the sandbox.",
     "params": {"path": ("string", "Folder path to create.")}, "required": ["path"]},
    {"name": "move", "description": "Move or rename a file or folder in the sandbox.",
     "params": {"from_path": ("string", "Existing path."), "to_path": ("string", "New path.")},
     "required": ["from_path", "to_path"]},
    {"name": "copy", "description": "Copy a file or folder in the sandbox.",
     "params": {"from_path": ("string", "Existing path."), "to_path": ("string", "New path.")},
     "required": ["from_path", "to_path"]},
    {"name": "delete", "description": "Delete a file or folder in the sandbox, including everything inside a folder.",
     "params": {"path": ("string", "Path to delete.")}, "required": ["path"]},
]


def run_tool(root, name, args):
    # Dispatch one model call. Always returns a dict; failures come back as {"error": ...} for the model to read.
    args = args if isinstance(args, dict) else {}
    try:
        if name == "list_folder":
            return list_dir(root, args.get("path", "/"))
        if name == "read_file":
            return read_file(root, args.get("path", ""))
        if name == "write_file":
            return write_file(root, args.get("path", ""), args.get("content", ""))
        if name == "make_folder":
            return make_dir(root, args.get("path", ""))
        if name == "move":
            return move(root, args.get("from_path", ""), args.get("to_path", ""))
        if name == "copy":
            return copy(root, args.get("from_path", ""), args.get("to_path", ""))
        if name == "delete":
            return delete(root, args.get("path", ""))
        return {"error": f"Unknown tool '{name}'."}
    except SandboxError as err:
        return {"error": str(err)}
    except OSError:
        return {"error": "The file system refused that action."}
