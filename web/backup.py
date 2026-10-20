# Which files a backup holds. A data backup has the server's state: the data folder, the character and
# group folders, and the server settings files. A full backup adds the server code, so it can rebuild the
# whole install. Encryption lives in web/secure.py; this module only chooses what goes in.
import datetime
import os
import sqlite3
import tempfile

KINDS = ("data", "full")
CODE_DIRS = ("web", "client")          # server code: full backups only
SKIP_DIRS = (".venv", "dist", ".git")  # build output and tooling, never backed up
STATE_FILES = (".env", "personas.json", "substitutions.json", "config.json", "device_id.json",
               "peers.json", "request_tracker.json", "token_tracker.json", "user_id.json")
SKIP_SUFFIXES = (".tmp", "-journal", "-wal", "-shm", ".pyc")
MAX_BYTES = 1024 * 1024 * 1024         # a backup is built in memory, so cap the size


class BackupError(ValueError):
    pass


def _skip_name(name):
    return name.startswith((".", "__")) or name.endswith(SKIP_SUFFIXES)


def _walk(base, arc_prefix):
    # Files under base as (arcname, path). Arcnames always use forward slashes.
    out = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if not _skip_name(d)]
        for fn in filenames:
            if _skip_name(fn):
                continue
            src = os.path.join(dirpath, fn)
            rel = os.path.relpath(src, base).replace(os.sep, "/")
            out.append((f"{arc_prefix}/{rel}" if arc_prefix else rel, src))
    return out


def snapshot_db(src, dst):
    # A consistent copy of the live database, taken with SQLite's backup API while the app keeps running.
    s = sqlite3.connect(src)
    try:
        d = sqlite3.connect(dst)
        try:
            s.backup(d)
        finally:
            d.close()
    finally:
        s.close()


def plan(char_root, data_dir, kind, db_snapshot):
    # Returns (entries, total_bytes). entries: [(arcname, path)]; db_snapshot replaces the live app.db.
    if kind not in KINDS:
        raise BackupError("Unknown backup kind.")
    entries = []
    data_abs = os.path.abspath(data_dir)
    for name in sorted(os.listdir(char_root)):
        path = os.path.join(char_root, name)
        # .env is a dotfile, but a backup has to carry it: it holds MISO_SECRET_KEY.
        if (_skip_name(name) and name != ".env") or name in SKIP_DIRS or os.path.abspath(path) == data_abs:
            continue
        if os.path.isdir(path):
            if kind == "data" and name in CODE_DIRS:
                continue
            entries += _walk(path, name)
        elif os.path.isfile(path):
            if kind == "data" and name not in STATE_FILES:
                continue
            entries.append((name, path))
    for arc, src in _walk(data_dir, "data"):
        if arc == "data/app.db":
            continue
        entries.append((arc, src))
    entries.append(("data/app.db", db_snapshot))
    total = sum(os.path.getsize(p) for _, p in entries)
    if total > MAX_BYTES:
        raise BackupError("The server data is over 1 GB, too large to back up from the web page.")
    return entries, total


def manifest(kind, entries, total, version):
    lines = [
        "kind: " + kind,
        "created: " + datetime.datetime.now().isoformat(timespec="seconds"),
        "version: " + (version or "unknown"),
        "files: " + str(len(entries)),
        "bytes: " + str(total),
        "",
        "Restore with restore_backup.py. Keep MISO_SECRET_KEY from .env: two-factor secrets need it.",
        "",
    ]
    return "\n".join(lines) + "\n".join(name for name, _ in entries) + "\n"


def make_snapshot_dir():
    # Temporary folder for the database copy, outside the project so it is never picked up.
    return tempfile.mkdtemp(prefix="miso-backup-")
