# The sanitisation scans, as tests: they walk the repository tree and fail when
# anything that must never reach a public release is present. Run from the
# project folder: python -m unittest discover -s tests
#
# Extra private terms (character, group and account names) are read from the
# file named by the MISO_PRIVATE_TERMS environment variable, one term per line,
# when that variable is set. The file stays outside the repository.

import ast
import json
import os
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

TEXT_SUFFIXES = {".py", ".js", ".md", ".txt", ".html", ".css", ".json",
                 ".webmanifest", ".svg", ".example", ".cfg", ".ini"}
SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", "data",
             "dist", ".release", ".hallmark", ".taste-skill", "users", ".opencode"}
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".webm",
                 ".woff", ".woff2", ".ttf", ".otf", ".zip", ".pdf", ".pyc"}

# Files and folders that hold local data or secrets and must never be committed.
FORBIDDEN_FILES = {".env", "device_id.json", "user_id.json", "peers.json",
                   "token_tracker.json", "request_tracker.json", "config.json",
                   "personas.json", "substitutions.json", "sandbox.json"}
FORBIDDEN_SUFFIXES = (".bak", ".p2pbak", ".pyc", ".pyo")

SECRETS = {
    "Google API key": re.compile(r"AIza[0-9A-Za-z_\-]{10,}"),
    "OpenAI-style key": re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    "GitHub token (gho_)": re.compile(r"gho_[A-Za-z0-9]{20,}"),
    "GitHub token (ghp_)": re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    "GitHub fine-grained token": re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    "Bearer token": re.compile(r"Bearer [A-Za-z0-9_\-\.=~+/]{20,}"),
}

PERSONAL = {
    "Private LAN address": re.compile(r"192\.168\.\d{1,3}\.\d{1,3}"),
    "Windows user path": re.compile(r"[CD]:\\+Users"),
    "Drive D path": re.compile(r"D:\\+[A-Za-z][A-Za-z0-9_\-\. ]*\\+"),
    "Shared sync user": re.compile("shared_character_" + "sync_user"),
}

# Phrases that must never appear in tracked text: the repository stays
# hand-written and free of AI attribution. The patterns are built from pieces
# so this file does not carry the phrases itself.
ATTRIBUTION = {
    "Co-author trailer": re.compile("(?i)co-" + "authored" + "-by"),
    "Generation trailer": re.compile("(?i)gene" + "rated (with|by)"),
    "Self-reference note": re.compile(r"(?i)\bai[- ]gene" + "rated\\b|\\bai[- ]assi" + "sted\\b"),
    "Tool mention": re.compile(r"(?i)\bchat" + "gpt\\b|\\bco" + "pilot\\b|\\bvibe[- ]co" + "ded\\b"),
}


def text_files():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.endswith(FORBIDDEN_SUFFIXES)]
        for name in filenames:
            path = Path(dirpath) / name
            if name in FORBIDDEN_FILES or name.endswith(FORBIDDEN_SUFFIXES):
                continue
            if path.suffix.lower() in TEXT_SUFFIXES:
                yield path


def read(path):
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return ""


def private_terms():
    # One term per line, # comments allowed. None when the variable is unset
    # or the file is missing, which skips the private-term check.
    name = os.environ.get("MISO_PRIVATE_TERMS")
    if not name:
        return None
    path = Path(name)
    if not path.is_file():
        return None
    terms = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            terms.append(line)
    return terms


class NoForbiddenFiles(unittest.TestCase):
    def test_no_data_or_secret_files_in_the_tree(self):
        found = []
        for dirpath, dirnames, filenames in os.walk(ROOT):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for name in filenames + dirnames:
                if name in FORBIDDEN_FILES or name.endswith(FORBIDDEN_SUFFIXES):
                    found.append(str(Path(dirpath) / name))
        self.assertEqual(found, [])

    def test_no_data_or_users_folders(self):
        found = []
        for dirpath, dirnames, _ in os.walk(ROOT):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for d in dirnames:
                if d in ("data", "users"):
                    found.append(str(Path(dirpath) / d))
        self.assertEqual(found, [])


class NoSecrets(unittest.TestCase):
    def test_no_key_material_in_text_files(self):
        hits = []
        for path in text_files():
            body = read(path)
            for label, pattern in SECRETS.items():
                if pattern.search(body):
                    hits.append("%s: %s" % (path.relative_to(ROOT), label))
        self.assertEqual(hits, [])


def term_matcher(term):
    # Same rule as the release gate: a plain word matches only on word
    # boundaries, so a short name does not fire inside an ordinary word.
    # Anything with a separator, a dot or a space matches as a substring.
    if re.fullmatch(r"[A-Za-z\u00c0-\u024f'\-]+", term):
        return re.compile(r"\b" + re.escape(term) + r"\b")
    return re.compile(re.escape(term))


class NoPersonalData(unittest.TestCase):
    def test_no_private_addresses_paths_or_names_in_text_files(self):
        hits = []
        for path in text_files():
            body = read(path)
            for label, pattern in PERSONAL.items():
                if pattern.search(body):
                    hits.append("%s: %s" % (path.relative_to(ROOT), label))
        self.assertEqual(hits, [])

    def test_no_private_terms_in_text_files(self):
        terms = private_terms()
        if not terms:
            self.skipTest("MISO_PRIVATE_TERMS is not set; the private-term scan is skipped")
        patterns = [(term, term_matcher(term)) for term in terms]
        hits = []
        for path in text_files():
            body = read(path)
            for term, pattern in patterns:
                if pattern.search(body):
                    hits.append("%s: %s" % (path.relative_to(ROOT), term))
        self.assertEqual(hits, [])


class NoAttribution(unittest.TestCase):
    def test_no_ai_attribution_phrases_in_text_files(self):
        hits = []
        for path in text_files():
            body = read(path)
            for label, pattern in ATTRIBUTION.items():
                if pattern.search(body):
                    hits.append("%s: %s" % (path.relative_to(ROOT), label))
        self.assertEqual(hits, [])


class SharedDefaults(unittest.TestCase):
    def test_the_public_substitutions_table_is_empty(self):
        # The private copy carries a few entries; the public one must ship none.
        source = read(ROOT / "rp_core.py")
        tree = ast.parse(source)
        found = None
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "DEFAULT_SUBSTITUTIONS":
                        found = node.value
        self.assertIsNotNone(found, "DEFAULT_SUBSTITUTIONS is not defined in rp_core.py")
        self.assertEqual(ast.literal_eval(found), {})


class DocsAgree(unittest.TestCase):
    def test_version_files_match(self):
        version = read(ROOT / "VERSION").strip()
        self.assertTrue(version, "VERSION is empty")
        changelog = read(ROOT / "CHANGELOG.md")
        self.assertIn(version, changelog, "CHANGELOG.md does not mention %s" % version)
        update = read(ROOT / "UPDATE.txt")
        self.assertIn(version, update, "UPDATE.txt does not mention %s" % version)

    def test_the_client_and_server_share_a_major_minor(self):
        version = read(ROOT / "VERSION").strip()
        client = read(ROOT / "client" / "miso_remote.py")
        numbers = re.findall(r'(?m)^\s*CLIENT_VERSION\s*=\s*"([^"]+)"', client)
        self.assertEqual(numbers, [version],
                         "client/miso_remote.py CLIENT_VERSION is %r, VERSION is %r"
                         % (numbers, version))


if __name__ == "__main__":
    unittest.main()
