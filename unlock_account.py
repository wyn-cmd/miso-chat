# Clears the sign-in lock on an account from the server. Use it when no admin can sign in.
import os
import sys

from dotenv_loader import load_dotenv
from web.store import Store

ROOT = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(ROOT, ".env"))
DATA_DIR = os.environ.get("MISO_DATA_DIR") or os.environ.get("ROLEPLAY_DATA_DIR") or os.path.join(ROOT, "data")


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: python unlock_account.py USERNAME")
    name = sys.argv[1].strip()
    store = Store(os.path.join(DATA_DIR, "app.db"))
    if store.find_user(name) is None and name.lower() not in store.login_throttle_all() and name.lower() not in store.blocked_ips_all():
        raise SystemExit(f"No account or sign-in lock called {name!r}.")
    store.unlock_account(name)
    print(f"Sign-in lock cleared for {name}.")


if __name__ == "__main__":
    main()
