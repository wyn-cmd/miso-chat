# Starts the Miso Chat server for the desktop shell.
#
# The Electron shell runs this program with piped stdio, waits for a line
#
#     MISO_URL=http://127.0.0.1:<port>/
#
# and then opens that address in its window. The process lives as long as the shell keeps its end
# of standard input open, so closing the window stops the server. All state goes to the user's own
# application-data folder, never next to the program: on macOS the app bundle is read-only.
#
# Run it by hand to try the desktop backend without the shell:
#
#     python desktop/launcher.py
#
# Options (all optional):
#     --port N            listen on this port; 0 picks a free one (the default)
#     --data-dir PATH     where app.db, uploads and secret.key live
#     --char-root PATH    where the character folders live
#
# The same two folders can be set with MISO_DESKTOP_DATA_DIR and MISO_DESKTOP_CHAR_ROOT, and a
# GEMINI_API_KEY already in the environment wins over the .env file.

import argparse
import os
import secrets
import shutil
import socket
import sys
import threading
import time

APP_NAME = "Miso Chat"
URL_PREFIX = "MISO_URL="


def app_root():
    # The folder holding web/, VERSION and the character folders. Inside a PyInstaller build this
    # is the unpacked bundle; running from source it is the project folder above desktop/.
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def user_data_dir():
    # The per-user folder the app owns: Windows %APPDATA%, macOS Application Support, Linux XDG.
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, APP_NAME)


def load_env(path):
    # Same rules as dotenv_loader: KEY=VALUE lines, '#' comments, the environment wins.
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


def write_first_run_env(path):
    # A template the user can fill in. Written once, and never over an existing file.
    if os.path.exists(path):
        return
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("# Miso Chat reads this file on every start.\n")
        fh.write("# Put your Gemini API key after the '=', save, and start the app again.\n")
        fh.write("GEMINI_API_KEY=\n")


def secret_key(folder):
    # The key that seals two-factor secrets. Kept beside the database, readable by this account only.
    path = os.path.join(folder, "secret.key")
    try:
        with open(path, encoding="utf-8") as fh:
            key = fh.read().strip()
        if key:
            return key
    except OSError:
        pass
    key = secrets.token_urlsafe(32)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(key)
    except OSError:
        pass   # a read-only folder still runs; two-factor setup then says it cannot store secrets
    return key


def seed_characters(char_root):
    # A build may carry starter characters. They are copied once, on first start only, so later
    # edits live in the user's folder and a new build never overwrites them.
    bundled = os.path.join(app_root(), "characters")
    if not os.path.isdir(bundled) or os.path.isdir(os.path.join(char_root, ".seeded")):
        return
    try:
        if os.listdir(char_root):
            return
        for name in os.listdir(bundled):
            src = os.path.join(bundled, name)
            if os.path.isdir(src) and not name.startswith("."):
                shutil.copytree(src, os.path.join(char_root, name))
        with open(os.path.join(char_root, ".seeded"), "w", encoding="utf-8") as fh:
            fh.write("starter characters copied\n")
    except OSError:
        pass


def free_port(preferred):
    # A free port on the loopback address. The shell reads the real port from the URL line, so a
    # busy port is never a reason to fail.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", preferred or 0))
            return s.getsockname()[1]
        except OSError:
            return 0


def wait_for_stdin_close():
    # The shell holds our standard input open for the life of its window. A closed pipe, or a
    # terminal that ends, is the signal to stop.
    try:
        while sys.stdin.readline():
            pass
    except (KeyboardInterrupt, OSError, ValueError):
        pass


def main(argv=None):
    root = app_root()
    parser = argparse.ArgumentParser(description="Run the Miso Chat server for the desktop shell.")
    parser.add_argument("--port", type=int, default=int(os.environ.get("MISO_DESKTOP_PORT") or 0))
    parser.add_argument("--data-dir", default=os.environ.get("MISO_DESKTOP_DATA_DIR") or user_data_dir())
    parser.add_argument("--char-root", default=os.environ.get("MISO_DESKTOP_CHAR_ROOT")
                        or os.path.join(user_data_dir(), "characters"))
    args = parser.parse_args(argv)

    data_dir = os.path.abspath(args.data_dir)
    char_root = os.path.abspath(args.char_root)
    try:
        os.makedirs(data_dir, exist_ok=True)
        os.makedirs(char_root, exist_ok=True)
    except OSError as err:
        print("MISO_ERROR=Cannot create the data folder: %s" % err, file=sys.stderr, flush=True)
        return 3

    load_env(os.path.join(data_dir, ".env"))
    load_env(os.path.join(root, ".env"))
    os.environ.setdefault("MISO_SECRET_KEY", secret_key(data_dir))
    # The window loads http://127.0.0.1, which browsers treat as secure but which is not https, so
    # the __Host- cookie prefix cannot be used here.
    os.environ["MISO_COOKIE_SECURE"] = "0"
    os.environ["MISO_DATA_DIR"] = data_dir

    if not os.environ.get("GEMINI_API_KEY"):
        env_path = os.path.join(data_dir, ".env")
        write_first_run_env(env_path)
        print("MISO_ERROR=Miso Chat needs a Gemini API key. Put it in %s and start the app again." % env_path,
              file=sys.stderr, flush=True)
        return 4

    seed_characters(char_root)
    if root not in sys.path:
        sys.path.insert(0, root)

    try:
        from web.app import make_app
        import uvicorn
    except Exception as err:   # a broken install should say so, not disappear
        print("MISO_ERROR=Cannot start Miso Chat: %s" % err, file=sys.stderr, flush=True)
        return 5

    try:
        app = make_app(data_dir=data_dir, char_root=char_root)
    except Exception as err:
        print("MISO_ERROR=Cannot start Miso Chat: %s" % err, file=sys.stderr, flush=True)
        return 5

    port = free_port(args.port)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning",
                            access_log=False, lifespan="off")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="server", daemon=True)
    thread.start()

    deadline = time.time() + 30
    while not server.started:
        if not thread.is_alive():
            print("MISO_ERROR=The server stopped while starting.", file=sys.stderr, flush=True)
            return 6
        if time.time() > deadline:
            print("MISO_ERROR=The server did not start within 30 seconds.", file=sys.stderr, flush=True)
            return 6
        time.sleep(0.05)

    print(URL_PREFIX + "http://127.0.0.1:%d/" % port, flush=True)
    wait_for_stdin_close()
    server.should_exit = True
    thread.join(timeout=10)
    return 0


if __name__ == "__main__":
    sys.exit(main())
