# Read KEY=VALUE lines from a .env file into the process environment.
# Blank lines and lines starting with # are skipped. A value already set in the environment wins,
# so a real environment variable always overrides the file. Nothing is printed.
import os


def load_dotenv(path):
    # Quietly does nothing when the file is missing, so a fresh copy still starts.
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            # empty values are skipped, so a blank line in .env leaves the variable unset
            if key and value and key not in os.environ:
                os.environ[key] = value
