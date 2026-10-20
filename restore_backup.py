# Restores an encrypted Miso Chat backup into an empty folder.
# Usage: python restore_backup.py BACKUP_FILE TARGET_FOLDER
# A data backup restores the data, characters and settings. A full backup also restores the server code.
# After restoring, install requirements.txt in that folder and start the app there.
import getpass
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from web import secure  # noqa: E402


def main(argv):
    if len(argv) != 3:
        print("Usage: python restore_backup.py BACKUP_FILE TARGET_FOLDER")
        return 2
    src, target = argv[1], os.path.abspath(argv[2])
    if not os.path.isfile(src):
        print("Backup file not found: " + src)
        return 1
    if os.path.isdir(target) and os.listdir(target):
        print("The target folder is not empty. Choose an empty folder.")
        return 1
    with open(src, "rb") as f:
        blob = f.read()
    password = getpass.getpass("Backup password: ")
    try:
        files = secure.decrypt_archive(password, blob)
    except secure.SecureError as err:
        print(str(err))
        return 1
    count = 0
    for name, data in files.items():
        if name == "MANIFEST.json":
            continue
        parts = name.split("/")
        if name.startswith("/") or ".." in parts:
            print("Refused a file with an unsafe path: " + name)
            return 1
        dest = os.path.join(target, *parts)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(data)
        count += 1
    print(f"Restored {count} files into {target}.")
    print("Next: pip install -r requirements.txt in that folder, then start the app from there.")
    print("Keep MISO_SECRET_KEY in .env: two-factor sign-in cannot open its secrets without it.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
