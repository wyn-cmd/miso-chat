"""Terminal client for a Miso Chat server, over its web API.

Standard library only. The password is typed at a prompt and kept only for the login call.
Character pictures need the chafa program (optional). Renders are cached under ~/.miso_remote.
"""
import argparse
import getpass
import glob
import hashlib
import http.cookiejar
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HEADER = {"X-Requested-With": "roleplay", "Content-Type": "application/json"}
RESET, BOLD, DIM, RED, GREEN = "\033[0m", "\033[1m", "\033[2m", "\033[31m", "\033[32m"
CLEAR = "\033[2J\033[H"
FALLBACK_WIDTH = 80
# Pictures are drawn to fit this box; the terminal width caps the columns.
PORTRAIT_ROWS = 40
PORTRAIT_MAX_COLS = 100
CACHE_DIR = os.path.join(os.path.expanduser("~"), ".miso_remote", "portraits")
# Keep in step with VERSION at the project root. A server must share this major.minor.
CLIENT_VERSION = "1.3.1"


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def _detail(err):
    try:
        payload = json.loads(err.read().decode("utf-8") or "{}")
    except (ValueError, OSError):
        payload = {}
    detail = payload.get("detail", payload.get("message", err.reason))
    if isinstance(detail, dict):
        return detail.get("message", str(detail))
    return str(detail)


def enable_ansi():
    # Older Windows consoles ignore ANSI colour codes until virtual terminal mode is switched on.
    if sys.platform != "win32":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    except (AttributeError, OSError):
        pass


def paint(hex_colour, text):
    # 24-bit colour from the server's #rrggbb value; plain text when there is none.
    m = re.fullmatch(r"#([0-9a-fA-F]{6})", hex_colour or "")
    if not m:
        return text
    r, g, b = (int(m.group(1)[i:i + 2], 16) for i in (0, 2, 4))
    return f"\033[1;38;2;{r};{g};{b}m{text}{RESET}"


def rule(name, colour=None):
    # A line like --[Name]------- that fills the terminal width.
    width = shutil.get_terminal_size((FALLBACK_WIDTH, 20)).columns
    head = f"--[{name}]"[:width]
    fill = "-" * (width - len(head))
    return f"{paint(colour, head)}{DIM}{fill}{RESET}"


def major_minor(version):
    # 1.3.0-beta and 1.3.2 both count as 1.3, so patch releases never block a connection.
    m = re.match(r"(\d+)\.(\d+)", version or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


class PortraitError(Exception):
    pass


def find_chafa():
    exe = shutil.which("chafa")
    if exe:
        return exe
    # A fresh winget install is not on PATH until the shell restarts, so look where it lands.
    hits = glob.glob(os.path.expandvars(
        r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\hpjansson.Chafa*\**\chafa.exe"), recursive=True)
    return hits[0] if hits else None


def _portrait_box():
    width = shutil.get_terminal_size((FALLBACK_WIDTH, 20)).columns
    return max(20, min(width - 2, PORTRAIT_MAX_COLS)), PORTRAIT_ROWS


def _image_suffix(data):
    if data[:4] == b"\x89PNG":
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ".img"


def _read_meta(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _chafa_render(exe, image, cols, rows):
    cmd = [exe, "-f", "symbols", "-c", "full", "--symbols", "braille",
           "--dither", "diffusion", "--size", f"{cols}x{rows}", image]
    try:
        done = subprocess.run(cmd, capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as err:
        raise PortraitError(f"chafa could not run: {err}")
    if done.returncode:
        raise PortraitError("chafa could not draw this picture.")
    # Cursor hide and show codes are not part of the picture; keep them out of the cache.
    text = done.stdout.decode("utf-8", "replace").replace("\r", "")
    return re.sub(r"\x1b\[\?[0-9;]*[hl]", "", text).rstrip("\n")


def portrait(client, name):
    # Returns the chafa render of a character's picture, or None when it has none. The image
    # is downloaded only when the server's hash differs from the saved one. The render is
    # redone only when the image or the box size changes.
    server_hash = client.icon_hash(name)
    if server_hash is None:
        return None
    exe = find_chafa()
    if exe is None:
        raise PortraitError("Pictures need chafa. Install it: winget install hpjansson.Chafa")
    os.makedirs(CACHE_DIR, exist_ok=True)
    key = hashlib.sha1(name.encode("utf-8")).hexdigest()[:16]
    meta_path = os.path.join(CACHE_DIR, key + ".json")
    meta = _read_meta(meta_path)
    image = os.path.join(CACHE_DIR, key + meta.get("suffix", ""))
    if meta.get("sha256") != server_hash or not meta.get("suffix") or not os.path.isfile(image):
        data = client.icon_bytes(name)
        if data is None:
            return None
        meta = {"sha256": hashlib.sha256(data).hexdigest(), "suffix": _image_suffix(data)}
        image = os.path.join(CACHE_DIR, key + meta["suffix"])
        with open(image, "wb") as f:
            f.write(data)
    cols, rows = _portrait_box()
    box = f"{cols}x{rows}"
    text_path = os.path.join(CACHE_DIR, key + ".txt")
    if meta.get("box") != box or not os.path.isfile(text_path):
        text = _chafa_render(exe, image, cols, rows)
        with open(text_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        meta["box"] = box
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f)
    with open(text_path, encoding="utf-8") as f:
        return f.read()


class _HttpsOnlyRedirects(urllib.request.HTTPRedirectHandler):
    # A redirect must not move the password onto plain http. Localhost stays allowed for testing.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urlparse(newurl)
        if target.scheme != "https" and target.hostname not in ("127.0.0.1", "localhost"):
            raise urllib.error.URLError("the server redirected to an address that is not https")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Client:
    def __init__(self, base):
        self.base = base
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), _HttpsOnlyRedirects())

    def _request(self, method, path, body=None, stream=False):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = dict(HEADER)
        if stream:
            headers["Accept"] = "text/event-stream"
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        return self.opener.open(req, timeout=600)

    def _json(self, method, path, body=None):
        try:
            with self._request(method, path, body) as r:
                return json.loads(r.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as err:
            raise ApiError(err.code, _detail(err))
        except urllib.error.URLError as err:
            raise ApiError(0, f"request stopped: {err.reason}")

    def check_version(self):
        # Runs before any password is asked for, so a mismatched client never sends one.
        try:
            info = self._json("GET", "/api/version")
            server = info.get("version", "") if isinstance(info, dict) else ""
        except ApiError as err:
            if err.status != 404:
                raise SystemExit(f"Could not check the server version: {err}")
            server = ""  # 404 means a server older than 1.3.0-beta, which does not report one
        except ValueError:
            server = ""  # a page that is not JSON, so not a Miso Chat API
        except OSError as err:
            raise SystemExit(f"Could not reach the server: {err}")
        theirs = major_minor(server)
        if theirs is None:
            raise SystemExit(
                f"This server does not report its version, so this client ({CLIENT_VERSION}) cannot confirm they match. "
                "The server is older than 1.3.0-beta; download the client from its web app instead.")
        if theirs != major_minor(CLIENT_VERSION):
            raise SystemExit(
                f"Version mismatch: this client is {CLIENT_VERSION} but the server runs {server}. "
                "They must share the same major and minor version. Download the client from this server's web app, which always matches it.")

    def login(self, username, password):
        return self._json("POST", "/api/login", {"username": username, "password": password, "remember": False})

    def login_2fa(self, code):
        # Finishes a sign-in that needed a code. The pending cookie from the password step is kept in the jar.
        return self._json("POST", "/api/login/2fa", {"code": code})

    def logout(self):
        try:
            self._json("POST", "/api/logout", {})
        except (ApiError, OSError):
            pass

    def me(self):
        return self._json("GET", "/api/me")

    def characters(self):
        return self._json("GET", "/api/characters")

    def icon_hash(self, name):
        try:
            return self._json("GET", "/api/icon/" + urllib.parse.quote(name) + "/hash")["sha256"]
        except ApiError as err:
            if err.status == 404:
                return None
            raise

    def icon_bytes(self, name):
        try:
            with self._request("GET", "/api/icon/" + urllib.parse.quote(name)) as r:
                return r.read()
        except urllib.error.HTTPError as err:
            if err.code == 404:
                return None
            raise ApiError(err.code, _detail(err))

    def history(self, character):
        return self._json("GET", "/api/history?" + urllib.parse.urlencode({"character": character})).get("messages", [])

    def memory(self, character):
        return self._json("GET", "/api/memory?" + urllib.parse.urlencode({"character": character}))

    def set_persona(self, name):
        return self._json("POST", "/api/persona", {"persona": name})

    def group_limits(self):
        return self._json("GET", "/api/groups")

    def create_group(self, name, members):
        return self._json("POST", "/api/groups", {"name": name, "members": members})

    def send(self, character, message, on_event):
        # Each SSE event is `event: <name>` then `data: <json>`, with a blank line after it.
        try:
            resp = self._request("POST", "/api/chat", {"character": character, "message": message}, stream=True)
        except urllib.error.HTTPError as err:
            raise ApiError(err.code, _detail(err))
        with resp:
            if "application/json" in (resp.headers.get("Content-Type") or ""):
                on_event("command", json.loads(resp.read().decode("utf-8")).get("command") or {})
                return
            event, data = "message", []
            for raw in resp:
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                if line == "":
                    if data:
                        on_event(event, json.loads("\n".join(data)))
                    event, data = "message", []
                elif line.startswith("event: "):
                    event = line[7:]
                elif line.startswith("data: "):
                    data.append(line[6:])


PLAIN = (False, False, False)


def _out(s):
    sys.stdout.write(s)
    sys.stdout.flush()


def sgr_for(style):
    # Full style each time: reset, then the attributes that are on.
    bold, italic, quote = style
    codes = ["0"]
    if bold:
        codes.append("1")
    if italic:
        codes.append("3")
    if quote:
        codes.append("32")
    return f"\x1b[{';'.join(codes)}m"


def fg_code(hex_colour):
    # Border colour from the character's #rrggbb; empty when the character has none.
    m = re.fullmatch(r"#([0-9a-fA-F]{6})", hex_colour or "")
    if not m:
        return ""
    r, g, b = (int(m.group(1)[i:i + 2], 16) for i in (0, 2, 4))
    return f"\x1b[38;2;{r};{g};{b}m"


def to_ansi(segments):
    # Joins (character, style) pairs; SGR codes are written only where the style changes.
    out, pen = [], PLAIN
    for ch, style in segments:
        if style != pen:
            out.append(sgr_for(style))
            pen = style
        out.append(ch)
    if pen != PLAIN:
        out.append(RESET)
    return "".join(out)


class TextStyler:
    # Turns chat text into (character, style) pairs, where style is (bold, italic, quote).
    # *x* is italic, **x** is bold and "quoted speech" is green. A star run waits for the next
    # character, because only then is it known whether it opens or closes a style.
    # A line break closes every open style, so an unclosed star cannot run past its line.
    def __init__(self):
        self.quote = self.italic = self.bold = False
        self.stars = 0
        self.prev = " "

    def _style(self):
        return (self.bold, self.italic, self.quote)

    def _marker(self, n, nxt):
        if n not in (1, 2):
            return [("*", self._style())] * n
        kind = "italic" if n == 1 else "bold"
        is_open = getattr(self, kind)
        # A marker opens at the start of a word and closes at the end of one, so a*b stays literal.
        starts_word = self.prev.isspace() or self.prev in "([{\"'"
        if is_open and not self.prev.isspace():
            setattr(self, kind, False)
        elif not is_open and starts_word and nxt and not nxt.isspace():
            setattr(self, kind, True)
        else:
            return [("*", self._style())] * n
        return []

    def feed(self, text):
        out = []
        for c in text:
            if c == "*":
                self.stars += 1
                continue
            if self.stars:
                out.extend(self._marker(self.stars, c))
                self.stars = 0
            if c == "\n":
                self.quote = self.italic = self.bold = False
            elif c == '"':
                self.quote = not self.quote
            out.append((c, self._style()))
            self.prev = c
        return out

    def finish(self):
        out = self._marker(self.stars, "") if self.stars else []
        self.stars = 0
        self.quote = self.italic = self.bold = False
        return out

    @staticmethod
    def segments(text):
        styler = TextStyler()
        return styler.feed(text) + styler.finish()

    @staticmethod
    def render(text):
        return to_ansi(TextStyler.segments(text))


class Box:
    # A rounded box for one character's reply, drawn as the text arrives. The top edge and
    # the left edge appear at once. Each row's right edge is drawn when the row ends, because
    # its length is not known until then. Words are held until a space or line break, so a
    # word wraps whole.
    def __init__(self, title, colour):
        self.width = max(24, shutil.get_terminal_size((FALLBACK_WIDTH, 20)).columns)
        self.inner = self.width - 4
        self.border = fg_code(colour)
        self.styler = TextStyler()
        self.word = []
        label = f" {title[:self.width - 8]} "
        dashes = self.width - 3 - len(label)
        _out(f"{self.border}╭─{BOLD}{label}{RESET}{self.border}{'─' * dashes}╮{RESET}\n")
        self._begin()

    def _begin(self):
        self.col = 0
        self.pen = PLAIN
        self.row_started = False

    def _put(self, seg):
        ch, style = seg
        if not self.row_started:
            _out(f"{self.border}│{RESET} ")
            self.row_started = True
        if style != self.pen:
            _out(sgr_for(style))
            self.pen = style
        _out(ch)
        self.col += 1

    def _finish(self):
        # Pad the row to the right edge and close it.
        if not self.row_started:
            _out(f"{self.border}│{RESET} ")
        if self.pen != PLAIN:
            _out(RESET)
        _out(" " * (self.inner - self.col) + f" {self.border}│{RESET}\n")

    def _end_line(self):
        self._finish()
        self._begin()

    def _flush_word(self):
        while self.word:
            room = self.inner - self.col
            if room <= 0:
                self._end_line()
                continue
            if len(self.word) <= room:
                for seg in self.word:
                    self._put(seg)
                self.word = []
                return
            if self.col > 0 and len(self.word) <= self.inner:
                self._end_line()
                continue
            # A word longer than the row is split across rows.
            for seg in self.word[:room]:
                self._put(seg)
            self.word = self.word[room:]
            self._end_line()

    def _segment(self, seg):
        ch = seg[0]
        if ch == "\n":
            self._flush_word()
            self._end_line()
        elif ch == " ":
            self._flush_word()
            if 0 < self.col < self.inner:
                self._put(seg)
        else:
            self.word.append(seg)

    def write_text(self, text):
        for seg in self.styler.feed(text):
            self._segment(seg)

    def close(self):
        for seg in self.styler.finish():
            self._segment(seg)
        self._flush_word()
        if self.row_started:
            self._finish()
        _out(f"{self.border}╰{'─' * (self.width - 2)}╯{RESET}\n")

class Session:
    def __init__(self, client):
        self.client = client
        self.solo, self.groups, self.personas = [], [], []
        self.colours = {}        # display name -> #rrggbb
        self.persona = None
        self.user_colour = None
        self.user_name = ""
        self.username = ""
        self.portrait_warned = False
        self.refresh_me()

    def refresh_me(self):
        me = self.client.me()
        self.username = me["username"]
        self.persona = (me.get("persona") or {}).get("name")
        self.user_colour = (me.get("persona") or {}).get("color")
        self.user_name = self.persona or self.username
        return me

    def load_catalog(self):
        data = self.client.characters()
        self.colours = data.get("colors", {})
        self.solo = data.get("solo", [])
        self.groups = data.get("groups", [])
        # The server sends personas as {"name", "color"} objects; the picker needs only the names.
        self.personas = [p["name"] for p in data.get("personas", [])]

    def catalog(self):
        return self.solo + [g["name"] for g in self.groups]

    def list_characters(self):
        self.load_catalog()
        print(f"{BOLD}Characters{RESET}")
        for i, name in enumerate(self.solo, 1):
            print(f"  {i:>2}. {paint(self.colours.get(name), name)}")
        if self.groups:
            print(f"{BOLD}Groups{RESET}")
        for j, g in enumerate(self.groups, len(self.solo) + 1):
            tag = " (yours)" if g.get("private") else ""
            members = ", ".join(g.get("members", []))
            print(f"  {j:>2}. {g['name']}{tag}: {members}")

    def resolve(self, raw):
        catalog = self.catalog()
        if raw.isdigit() and 1 <= int(raw) <= len(catalog):
            return catalog[int(raw) - 1]
        for name in catalog:
            if name.lower() == raw.lower():
                return name
        return None

    def colour_of(self, speaker):
        return self.colours.get(speaker)

    def print_message(self, speaker, text, role):
        if role == "user":
            print(rule(self.user_name, self.user_colour))
            print(TextStyler.render(text))
        else:
            box = Box(speaker, self.colour_of(speaker))
            box.write_text(text)
            box.close()

    def recap(self, character):
        msgs = self.client.history(character)
        if len(msgs) >= 2:
            print(f"{DIM}--- Last 2 messages ---{RESET}")
            for m in msgs[-2:]:
                self.print_message(m["speaker"], m["text"], m["role"])
            print(f"{DIM}------------------------{RESET}\n")

    def show_history(self, character):
        msgs = self.client.history(character)
        print(f"{BOLD}Chat history ({len(msgs)} messages){RESET}")
        for m in msgs:
            self.print_message(m["speaker"], m["text"], m["role"])
        print(f"{DIM}--- end of chat ---{RESET}")

    def show_memory(self, character):
        data = self.client.memory(character)
        print(f"{BOLD}Memory bank{RESET} {DIM}(limit {data.get('limit')} characters){RESET}")
        print(data.get("memory") or f"{DIM}(empty){RESET}")
        print(f"\n{BOLD}Pinned facts{RESET}")
        print(data.get("pinned") or f"{DIM}(none){RESET}")

    def pick_persona(self):
        self.load_catalog()
        names = list(self.personas)
        if not names:
            print("No personas are set up on this server.")
            return
        print(f"{BOLD}Personas{RESET} (you are {self.user_name})")
        print(f"   0. none{'  <- current' if not self.persona else ''}")
        for i, name in enumerate(names, 1):
            mark = "  <- current" if name == self.persona else ""
            print(f"  {i:>2}. {paint(self.colours.get(name), name)}{mark}")
        raw = input("Persona (number or name, blank to keep): ").strip()
        if not raw:
            return
        if raw == "0" or raw.lower() == "none":
            choice = ""
        elif raw.isdigit() and 1 <= int(raw) <= len(names):
            choice = names[int(raw) - 1]
        else:
            matches = [n for n in names if n.lower() == raw.lower()]
            if not matches:
                print("No such persona.")
                return
            choice = matches[0]
        self.apply_persona(choice)

    def apply_persona(self, choice):
        try:
            self.client.set_persona(choice)
        except ApiError as err:
            print(f"{RED}! {err}{RESET}")
            return False
        self.refresh_me()
        print(f"Playing as {self.user_name}.")
        return True

    def new_group(self):
        self.load_catalog()
        limits = self.client.group_limits()
        lo, hi = limits.get("min_members", 2), limits.get("max_members", 8)
        name = input("Group name (blank to cancel): ").strip()
        if not name:
            return None
        print("Pick the characters for the group:")
        for i, n in enumerate(self.solo, 1):
            print(f"  {i:>2}. {n}")
        raw = input(f"Members ({lo} to {hi}, numbers or names separated by commas): ")
        members = []
        for part in (p.strip() for p in raw.split(",")):
            if not part:
                continue
            if part.isdigit() and 1 <= int(part) <= len(self.solo):
                found = self.solo[int(part) - 1]
            else:
                found = next((n for n in self.solo if n.lower() == part.lower()), None)
            if found is None:
                print(f"No character called {part!r}. Group not made.")
                return None
            if found not in members:
                members.append(found)
        if not lo <= len(members) <= hi:
            print(f"A group needs {lo} to {hi} characters. Group not made.")
            return None
        try:
            res = self.client.create_group(name, members)
        except ApiError as err:
            print(f"{RED}! {err}{RESET}")
            return None
        print(f"Group made: {res['name']} ({len(members)} characters).")
        return res["name"]

    def help(self, in_chat):
        print(f"{BOLD}Commands{RESET}")
        if in_chat:
            rows = [
                ("/model [name|auto]", "show or pin the model (server)"),
                ("/save", "refresh the memory bank now (server)"),
                ("/back or /undo", "remove the last exchange (server)"),
                ("/history", "print the whole chat"),
                ("/portrait", "show this character's picture"),
                ("/memory", "show the memory bank and pinned facts"),
                ("/persona", "pick the persona you play as"),
                ("/characters", "list characters and groups"),
                ("/clear", "clear the screen"),
                ("/switch", "go back to the list"),
                ("/exit", "log out and quit"),
            ]
        else:
            rows = [
                ("<number or name>", "open a character or group"),
                ("/characters", "list characters and groups"),
                ("/persona", "pick the persona you play as"),
                ("/group new", "make a private group from characters"),
                ("/clear", "clear the screen"),
                ("/exit", "log out and quit"),
            ]
        for cmd, desc in rows:
            print(f"  {cmd:<20} {desc}")

    def show_portrait(self, character, from_open=False):
        try:
            text = portrait(self.client, character)
        except PortraitError as err:
            # Say once when a chat opens that pictures need chafa; /portrait always says why.
            if not (from_open and self.portrait_warned):
                print(f"{DIM}({err}){RESET}")
            self.portrait_warned = True
            return
        except (ApiError, OSError) as err:
            print(f"{DIM}(picture unavailable: {err}){RESET}")
            return
        if text is None:
            if not from_open:
                print(f"{DIM}(no picture for this character){RESET}")
            return
        print(text)
        print()

    def chat(self, character):
        self.show_portrait(character, from_open=True)
        self.recap(character)
        print(f"Chatting with {paint(self.colour_of(character), character)}. /help for commands.\n")
        while True:
            print(rule(self.user_name, self.user_colour))
            try:
                text = input("> ").strip()
            except EOFError:
                return "exit"
            low = text.lower()
            if not text:
                continue
            if low in ("/exit", "/quit", "exit", "quit"):
                return "exit"
            if low == "/switch":
                return "switch"
            if low == "/help":
                self.help(in_chat=True)
            elif low == "/characters":
                self.list_characters()
            elif low == "/history":
                self.show_history(character)
            elif low == "/memory":
                self.show_memory(character)
            elif low == "/persona":
                self.pick_persona()
            elif low == "/portrait":
                self.show_portrait(character)
            elif low == "/clear":
                print(CLEAR, end="")
            else:
                self.send(character, text)

    def send(self, character, text):
        state = {"speaker": None, "writer": None, "shown": {}, "replies": [], "started": time.time(), "model": None}

        def on_event(event, payload):
            if event == "delta":
                speaker, body = payload.get("speaker") or "", payload.get("text") or ""
                seen = state["shown"].get(speaker, "")
                if state["speaker"] != speaker:
                    if state["writer"]:
                        state["writer"].close()
                    state["speaker"], state["writer"] = speaker, Box(speaker, self.colour_of(speaker))
                    seen = ""
                if body.startswith(seen) and len(body) > len(seen):
                    state["writer"].write_text(body[len(seen):])
                    state["shown"][speaker] = body
            elif event == "done":
                state["replies"] = payload.get("replies", [])
                if state["writer"]:
                    state["writer"].close()
                    state["writer"] = None
                elif state["replies"]:
                    for r in state["replies"]:
                        box = Box(r.get("speaker", ""), r.get("color") or self.colour_of(r.get("speaker", "")))
                        box.write_text(r.get("text", ""))
                        box.close()
                if state["replies"]:
                    state["model"] = state["replies"][-1].get("model")
                if payload.get("selector_note"):
                    print(f"{DIM}({payload['selector_note']}){RESET}")
            elif event == "usage":
                took = time.time() - state["started"]
                print(f"{DIM}Model: {state['model'] or 'none'} | {took:.1f}s | "
                      f"Requests: {payload.get('requests_today')}/{payload.get('request_cap')}{RESET}")
            elif event == "error":
                if state["writer"]:
                    state["writer"].close()
                    state["writer"] = None
                print(f"\n{RED}! {payload.get('message', 'Something went wrong.')}{RESET}")
            elif event == "command":
                notice = payload.get("notice")
                if notice:
                    # Server notices use Markdown marks; style them instead of showing the raw marks.
                    print(TextStyler.render(notice.replace("`", "")))
                if payload.get("reload"):
                    print(f"{DIM}(chat changed on the server; /history shows it){RESET}")

        try:
            self.client.send(character, text, on_event)
        except ApiError as err:
            if err.status == 401:
                raise
            print(f"{RED}! {err}{RESET}")
        except OSError as err:
            print(f"{RED}! The connection dropped ({err}). Your message may still be saved on the server.{RESET}")

    def menu(self):
        self.list_characters()
        print()
        while True:
            try:
                raw = input("menu> ").strip()
            except EOFError:
                return None
            low = raw.lower()
            if not raw:
                continue
            if low in ("/exit", "/quit", "exit", "quit"):
                return None
            if low == "/help":
                self.help(in_chat=False)
            elif low == "/characters":
                self.list_characters()
            elif low == "/persona":
                self.pick_persona()
            elif low == "/group new":
                made = self.new_group()
                if made:
                    return made
            elif low == "/clear":
                print(CLEAR, end="")
            else:
                name = self.resolve(raw)
                if name:
                    return name
                print("No such character. /characters lists them.")


def finish_sign_in(client, info, ask):
    # The password only opens a pending session when two-factor sign-in is on; a code finishes it.
    # A first sign-in with it on also gets the key to add to an authenticator app, shown here once.
    if not info.get("setup_2fa") and not info.get("otp_required"):
        return info
    if info.get("setup_2fa"):
        secret = info.get("secret", "")
        grouped = " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))
        print("Two-factor sign-in is on for this account. Add it to an authenticator app")
        print("(Google Authenticator, Aegis, 1Password and similar) by typing this key in:")
        print("    " + grouped)
        print("The key is shown only now. Then enter the 6-digit code the app shows.\n")
    else:
        print("Two-factor sign-in: enter the 6-digit code from your authenticator app.")
    for _ in range(3):
        try:
            return client.login_2fa(ask("Code: ").strip())
        except ApiError as err:
            # Only a wrong code is worth asking again. Anything else (lock, expired sign-in) stops here.
            if err.status != 401 or not str(err).startswith("That code is wrong"):
                raise
            print(err)
    raise SystemExit("Too many wrong codes. Sign in again.")


def main():
    enable_ansi()
    parser = argparse.ArgumentParser(description="Chat with your characters on a Miso Chat server.")
    parser.add_argument("server", help="server address, for example https://your-server.example")
    parser.add_argument("--character", help="open this character or group straight away")
    parser.add_argument("--persona", help="play as this persona from the start")
    parser.add_argument("--username", help="account name (asked for if not given)")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    base = args.server.rstrip("/")
    host = urllib.parse.urlparse(base).hostname or ""
    if not base.startswith("https://") and host not in ("127.0.0.1", "localhost"):
        raise SystemExit("Use an https:// address for a remote server.")

    client = Client(base)
    client.check_version()
    username = args.username or input("Username: ").strip()
    password = getpass.getpass("Password: ")
    try:
        info = client.login(username, password)
        info = finish_sign_in(client, info, input)
    except ApiError as err:
        raise SystemExit(f"Login failed: {err}")
    finally:
        password = None
    if info.get("must_change"):
        client.logout()
        raise SystemExit("This account must change its password first. Do that in the web app, then run this again.")

    session = Session(client)
    session.load_catalog()
    if args.persona:
        matches = [p for p in session.personas if p.lower() == args.persona.lower()]
        if not matches:
            client.logout()
            raise SystemExit(f"No persona called {args.persona!r} on this server.")
        session.apply_persona(matches[0])
    print(f"Logged in as {session.username}. Playing as {session.user_name}.\n")

    try:
        wanted = args.character
        while True:
            if wanted:
                character = session.resolve(wanted)
                if character is None:
                    raise SystemExit(f"No character or group called {wanted!r} on this server.")
                wanted = None
            else:
                character = session.menu()
            if character is None:
                break
            if session.chat(character) == "exit":
                break
    except ApiError as err:
        if err.status == 401:
            print(f"{RED}The session ended. Run the client again to log in.{RESET}")
        else:
            print(f"{RED}! {err}{RESET}")
    except (KeyboardInterrupt, EOFError):
        print()
    finally:
        client.logout()
    print("Bye.")


if __name__ == "__main__":
    main()
