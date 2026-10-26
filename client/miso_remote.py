# Terminal client for a Miso Chat server, over its web API.
#
# Standard library only. The password is typed at a prompt and kept only for the login call.
# Character pictures need the chafa program (optional). Renders are cached under ~/.miso_remote.
import argparse
import codecs
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
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

HEADER = {"X-Requested-With": "roleplay", "Content-Type": "application/json"}
RESET, BOLD, DIM = "\033[0m", "\033[1m", "\033[2m"
RED, GREEN, YELLOW = "\033[31m", "\033[32m", "\033[33m"
CLEAR = "\033[2J\033[H"
HIDE_CURSOR, SHOW_CURSOR = "\033[?25l", "\033[?25h"
# Synchronized output: a terminal that supports it paints each frame at once, and others ignore it.
SYNC_START, SYNC_END = "\033[?2026h", "\033[?2026l"
FALLBACK_WIDTH = 80
FALLBACK_HEIGHT = 24
# Pictures are drawn to fit this box; the terminal width caps the columns. A taller box on a big
# window gives the chafa render more cells to work with, and so more definition.
PORTRAIT_ROWS = 66
PORTRAIT_MAX_COLS = 164
CACHE_DIR = os.path.join(os.path.expanduser("~"), ".miso_remote", "portraits")
# Terminal-only preferences, such as which token window the status bar shows.
SETTINGS_PATH = os.path.join(os.path.expanduser("~"), ".miso_remote", "settings.json")
USAGE_BAR_KINDS = ("daily", "weekly", "monthly")
DEFAULT_USAGE_BAR = "daily"
# Keep in step with VERSION at the project root. A server must share this major.minor.
CLIENT_VERSION = "1.5.0"


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


def ask_password(prompt="Password: "):
    # Reads a password with no echo and one * per character typed, so the length is visible.
    # A piped session cannot redraw a * or hide anything useful, so it falls back to getpass.
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return getpass.getpass(prompt)
    _out(prompt)
    chars = []
    try:
        if sys.platform == "win32":
            _read_password_windows(chars)
        else:
            _read_password_posix(chars)
    finally:
        _out("\n")
    return "".join(chars)


def _read_password_windows(chars):
    import msvcrt
    while True:
        ch = msvcrt.getwch()
        if ch in ("\r", "\n"):
            return
        if ch == "\x03":
            raise KeyboardInterrupt
        if ch in ("\x08", "\x7f"):
            if chars:
                chars.pop()
                _out("\b \b")
            continue
        if ch in ("\x00", "\xe0"):
            msvcrt.getwch()      # the second half of a special key, such as an arrow
            continue
        if "\ud800" <= ch <= "\udbff":
            # A pasted or typed emoji arrives as two UTF-16 halves; join them back into one character.
            try:
                pair = ch + msvcrt.getwch()
                ch = pair.encode("utf-16-le", "surrogatepass").decode("utf-16-le")
            except (OSError, UnicodeDecodeError):
                continue
        if ch < " ":
            continue
        chars.append(ch)
        _out("*")


def _read_password_posix(chars):
    # cbreak keeps Ctrl+C as a signal, so the caller's finally can restore the terminal.
    import termios
    import tty
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    try:
        tty.setcbreak(fd)
        while True:
            data = os.read(fd, 1)
            if not data:
                return            # end of input: accept what was typed
            ch = decoder.decode(data)
            if not ch:
                continue          # the first byte of a multi-byte character
            if ch in ("\r", "\n"):
                return
            if ch in ("\x7f", "\x08"):
                if chars:
                    chars.pop()
                    _out("\b \b")
                continue
            if ch < " ":
                continue
            chars.append(ch)
            _out("*")
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


def paint(hex_colour, text):
    # 24-bit colour from the server's #rrggbb value; plain text when there is none.
    m = re.fullmatch(r"#([0-9a-fA-F]{6})", hex_colour or "")
    if not m:
        return text
    r, g, b = (int(m.group(1)[i:i + 2], 16) for i in (0, 2, 4))
    return f"\033[1;38;2;{r};{g};{b}m{text}{RESET}"


def term_size():
    return shutil.get_terminal_size((FALLBACK_WIDTH, FALLBACK_HEIGHT))


def cell_width(text):
    # Columns a plain string takes in the terminal: wide characters (CJK, emoji) take two.
    width = 0
    for c in text:
        if unicodedata.combining(c):
            continue
        width += 2 if unicodedata.east_asian_width(c) in ("W", "F") else 1
    return width


def clip(text, limit):
    return text if len(text) <= limit else text[:max(1, limit - 1)] + "…"


def short_number(n):
    # 1234 -> 1.2k, 12400 -> 12k, 999999 -> 1.0M.
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "?"
    if n >= 999_950:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        value = n / 1000
        return f"{value:.0f}k" if value >= 10 else f"{value:.1f}k"
    return str(n)


def load_settings():
    # Local preferences for this machine. A damaged or missing file means the defaults, never an error.
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_setting(key, value):
    data = load_settings()
    data[key] = value
    os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
    tmp = SETTINGS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, SETTINGS_PATH)


def usage_meter(usage, kind=DEFAULT_USAGE_BAR):
    # The chosen token window as (kind, used, limit, share): daily unless the user picked another.
    # When that window has no limit for this account, the tightest window that does is used instead,
    # so an admin or an unmetered account still sees nothing while a metered one always sees a bar.
    budgets = (usage or {}).get("token_budgets") or {}
    chosen = budgets.get(kind)
    if isinstance(chosen, dict) and chosen.get("limit"):
        limit = int(chosen["limit"])
        used = int(chosen.get("used", 0))
        return kind, used, limit, used / limit
    best = None
    for name in USAGE_BAR_KINDS:
        window = budgets.get(name)
        if not isinstance(window, dict) or not window.get("limit"):
            continue
        limit = int(window["limit"])
        used = int(window.get("used", 0))
        share = used / limit
        if best is None or share > best[3]:
            best = (name, used, limit, share)
    return best


def prompt_estimate(context, typed=""):
    # Tokens the next prompt should carry: the system prompt, the stored messages in the rolling
    # window, and what is in the input line. Same arithmetic as Engine.context_estimate and the web app.
    if not context:
        return None
    typed_tokens = (len(typed) + 3) // 4
    return (context.get("system_tokens") or 0) + (context.get("window_tokens") or 0) + typed_tokens


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
    size = term_size()
    # The picture takes nearly all the window above the prompt: the heading, two messages and the
    # prompt need about eight rows, so the picture gets the rest, up to PORTRAIT_ROWS. The chafa box
    # is capped at PORTRAIT_MAX_COLS so a very wide window still leaves the picture centred.
    rows = max(6, min(PORTRAIT_ROWS, size.lines - 8))
    return max(20, min(size.columns - 2, PORTRAIT_MAX_COLS)), rows


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

    def context(self, character):
        # Estimated prompt size for the next message: see Engine.context_estimate.
        return self._json("GET", "/api/context?" + urllib.parse.urlencode({"character": character}))

    def history(self, character):
        return self._json("GET", "/api/history?" + urllib.parse.urlencode({"character": character})).get("messages", [])

    def memory(self, character):
        return self._json("GET", "/api/memory?" + urllib.parse.urlencode({"character": character}))

    def set_persona(self, name):
        return self._json("POST", "/api/persona", {"persona": name})

    def palace(self, character):
        # This account's Mind Palace for one character; the first read copies the character's starting one.
        return self._json("GET", "/api/character-palace?" + urllib.parse.urlencode({"character": character}))

    def set_palace(self, character, entries, recursive=None, budget=None):
        # Saves the whole entry list. The settings are sent back as read, so an entry never resets them.
        body = {"entries": entries}
        if recursive is not None:
            body["recursive"] = recursive
        if budget is not None:
            body["budget"] = budget
        return self._json("POST", "/api/character-palace?" + urllib.parse.urlencode({"character": character}), body)

    def set_settings(self, body):
        return self._json("POST", "/api/settings", body)

    def group_limits(self):
        return self._json("GET", "/api/groups")

    def create_group(self, name, members):
        return self._json("POST", "/api/groups", {"name": name, "members": members})

    def autofill(self, character):
        # The server drafts the user's next message; nothing is saved to the chat until it is sent.
        return self._json("POST", "/api/autofill", {"character": character})

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


class Message:
    # One message: a bullet and the speaker's name in the speaker's colour, with the text
    # wrapped under them. The name appears at once; the text follows as it arrives, a word
    # at a time, so a word is never split between rows. No borders are drawn.
    INDENT = 2

    def __init__(self, speaker, colour):
        self.width = max(20, term_size().columns)
        self.room = max(8, self.width - self.INDENT)
        self.styler = TextStyler()
        self.word = []
        self.col = 0
        self.pen = PLAIN
        self.row_started = False
        name = speaker[:max(1, self.width - 4)]
        if colour:
            _out(f"{paint(colour, '●')} {paint(colour, name)}\n")
        else:
            _out(f"● {BOLD}{name}{RESET}\n")

    def _begin(self):
        self.col = 0
        self.pen = PLAIN
        self.row_started = False

    def _put(self, seg):
        ch, style = seg
        if not self.row_started:
            _out(" " * self.INDENT)
            self.row_started = True
        if style != self.pen:
            _out(sgr_for(style))
            self.pen = style
        _out(ch)
        self.col += cell_width(ch)

    def _end_line(self):
        if self.pen != PLAIN:
            _out(RESET)
        _out("\n")
        self._begin()

    def _word_width(self):
        return sum(cell_width(seg[0]) for seg in self.word)

    def _flush_word(self):
        while self.word:
            room = self.room - self.col
            if room <= 0:
                self._end_line()
                continue
            if self._word_width() <= room:
                for seg in self.word:
                    self._put(seg)
                self.word = []
                return
            if self.col > 0:
                self._end_line()
                continue
            # A word wider than the whole row is split across rows.
            moved = False
            while self.word:
                width = cell_width(self.word[0][0])
                if moved and width > room:
                    break
                self._put(self.word.pop(0))
                room -= width
                moved = True
                if room <= 0:
                    break
            self._end_line()

    def _segment(self, seg):
        ch = seg[0]
        if ch == "\n":
            self._flush_word()
            self._end_line()
        elif ch == " ":
            self._flush_word()
            if 0 < self.col < self.room:
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
        if not self.row_started:
            _out("\n")
        else:
            self._end_line()
            _out("\n")   # a second blank line between messages, so each one stands apart
        _out("\n")


class KeyReader:
    # Reads keys one at a time, with no echo, and names the ones the composer acts on.
    # Windows reads through msvcrt; elsewhere the terminal is put in cbreak mode. The menus of
    # the client keep plain input(); this is only open while a chat is.

    CONTROL = {"\x03": "ctrl-c", "\x04": "ctrl-d", "\x15": "ctrl-u", "\x17": "ctrl-w",
               "\x01": "ctrl-a", "\x05": "ctrl-e", "\x0b": "ctrl-k", "\x0c": "ctrl-l"}
    CSI = {"A": "up", "B": "down", "C": "right", "D": "left", "H": "home", "F": "end", "Z": "tab"}
    TILDE = {"1": "home", "3": "delete", "4": "end", "5": "pageup", "6": "pagedown",
             "200": "paste-start", "201": "paste-end"}

    def __init__(self):
        self.windows = sys.platform == "win32"
        self.fd = None
        self.saved = None
        self.pending = []
        self.last_key = None
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")

    def __enter__(self):
        if not self.windows:
            import termios
            import tty
            self.fd = sys.stdin.fileno()
            self.saved = termios.tcgetattr(self.fd)
            tty.setcbreak(self.fd)
            # Bracket pastes, so a line break in a pasted paragraph is not taken as a send.
            _out("\x1b[?2004h")
        return self

    def __exit__(self, *exc):
        if not self.windows:
            import termios
            _out("\x1b[?2004l")
            if self.saved is not None:
                termios.tcsetattr(self.fd, termios.TCSADRAIN, self.saved)
                self.saved = None
        self.pending = []
        return False

    def read(self, timeout=None):
        # One key, or None when nothing arrived before the timeout.
        if self.pending:
            return self.pending.pop(0)
        return self._read_windows(timeout) if self.windows else self._read_posix(timeout)

    def _read_windows(self, timeout):
        import msvcrt
        end = None if timeout is None else time.monotonic() + timeout
        while not msvcrt.kbhit():
            if end is not None and time.monotonic() >= end:
                return None
            time.sleep(0.02)
        ch = msvcrt.getwch()
        now = time.monotonic()
        # A paste reaches the console as a burst of keys, far faster than a person types.
        burst = self.last_key is not None and now - self.last_key < 0.03
        self.last_key = now
        if ch in ("\x00", "\xe0"):
            return {"H": "up", "P": "down", "K": "left", "M": "right", "G": "home",
                    "O": "end", "S": "delete", "I": "pageup", "Q": "pagedown"}.get(msvcrt.getwch(), "")
        if "\ud800" <= ch <= "\udbff":
            # An emoji arrives as two UTF-16 halves; join them back into one character.
            try:
                pair = ch + msvcrt.getwch()
                ch = pair.encode("utf-16-le", "surrogatepass").decode("utf-16-le")
            except (OSError, UnicodeDecodeError):
                return ""
        if ch in ("\r", "\n"):
            # The console offers no bracket marks for a paste, so a line break that arrives inside a
            # burst of keys is taken as text rather than a send. A CRLF pair counts once.
            if burst and msvcrt.kbhit():
                follow = msvcrt.getwch()
                if follow in ("\r", "\n"):
                    pass
                elif follow in ("\x00", "\xe0"):
                    msvcrt.getwch()      # the rest of a special key, which a paste cannot carry
                else:
                    self.pending.insert(0, follow)
                return "\n"
            return "enter"
        if ch in ("\x08", "\x7f"):
            return "backspace"
        if ch == "\t":
            return "tab"
        if ch == "\x1b":
            return "escape"
        if ch in self.CONTROL:
            return self.CONTROL[ch]
        return "" if ord(ch[:1]) < 32 else ch

    def _read_posix(self, timeout):
        import select
        if self.fd is None:
            self.fd = sys.stdin.fileno()
        ready, _, _ = select.select([self.fd], [], [], timeout)
        if not ready:
            return None
        data = os.read(self.fd, 256)
        if not data:
            return "eof"
        self.pending.extend(self._parse(self.decoder.decode(data)))
        return self.pending.pop(0) if self.pending else None

    def _parse(self, chars):
        # One read can hold a whole pasted line, so an escape sequence, or a run of text, is split here.
        out, i = [], 0
        while i < len(chars):
            c = chars[i]
            if c == "\x1b":
                m = re.match(r"\x1b\[(\d*)(?:;(\d+))?([A-Za-z~])", chars[i:])
                if m:
                    first, final = m.group(1), m.group(3)
                    if final == "~":
                        out.append(self.TILDE.get(first, ""))
                    elif first == "1" and final in ("A", "B"):
                        out.append("home" if final == "A" else "end")
                    else:
                        out.append(self.CSI.get(final, ""))
                    i += m.end()
                    continue
                out.append("escape")
            elif c in ("\r", "\n"):
                out.append("enter")
            elif c in ("\x7f", "\x08"):
                out.append("backspace")
            elif c == "\t":
                out.append("tab")
            elif c in self.CONTROL:
                out.append(self.CONTROL[c])
            elif c >= " ":
                out.append(c)
            i += 1
        return out


class Spinner:
    # A one-line "working" mark for the wait before the first token. halt() is called before
    # anything else is printed, so the mark never runs into the reply.

    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, label):
        self.label = label
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.thread = None
        self.running = False

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._spin, daemon=True)
        self.thread.start()

    def _spin(self):
        i = 0
        while not self.stop.is_set():
            frame = self.FRAMES[i % len(self.FRAMES)]
            with self.lock:
                if self.stop.is_set():
                    return
                _out(f"\r{DIM}{frame} {self.label}{RESET}\x1b[K")
            i += 1
            self.stop.wait(0.09)

    def halt(self):
        if not self.running:
            return
        self.stop.set()
        self.thread.join(timeout=0.3)
        with self.lock:
            _out("\r\x1b[K")
        self.running = False


class Composer:
    # The prompt at the bottom of a chat, drawn as a rounded box the way Codewhale's is.
    #
    # The box spans the window width. The character's name sits in the top border, the typed line
    # follows an accent ❯, and the status row (model, clock, token meter, next prompt size) sits in
    # the bottom border. The box redraws in place, so the chat scrolls above it, and it is redrawn
    # while idle when the window is resized or the clock moves on.

    GLYPH = "❯"
    PREFIX = 2          # the glyph and a space; continuation rows line up under the text
    EDGE = 2            # the border and its inner space, on each side

    def __init__(self, status_fn, label="", colour=None):
        self.status_fn = status_fn
        self.label = label or ""      # shown in the top border, usually the character's name
        self.colour = colour          # the accent for the glyph and the name
        self.keys = KeyReader()
        self.text = []
        self.cursor = 0
        self.drawn = 0
        self.cursor_row = 0
        self.history = []
        self.hist = 0
        self.pasting = False
        self.interactive = bool(sys.stdin.isatty() and sys.stdout.isatty())

    def read(self, initial=""):
        # One line of input. Raises KeyboardInterrupt on an empty line's Ctrl+C, EOFError on Ctrl+D.
        # initial is text put in the prompt to be edited, as /autofill does with its draft.
        self.text, self.cursor = list(initial), len(initial)
        self.hist, self.pasting = len(self.history), False
        if not self.interactive:
            return self._read_plain(initial)
        with self.keys:
            try:
                self._render()
                size, clock = term_size(), time.strftime("%H:%M")
                while True:
                    try:
                        key = self.keys.read(0.25)
                    except KeyboardInterrupt:
                        key = "ctrl-c"
                    if key is None:
                        # An idle prompt still follows the window and the clock, the way a coding
                        # agent's footer does: a resize or a new minute redraws it in place.
                        now, minute = term_size(), time.strftime("%H:%M")
                        if now != size or minute != clock:
                            size, clock = now, minute
                            self._render()
                        continue
                    line = self._apply(key)
                    if line is not None:
                        return line
            finally:
                self._erase()

    def _read_plain(self, initial=""):
        status_plain, status_styled = self.status_fn("", term_size().columns)
        if status_plain:
            print(status_styled or status_plain, file=sys.stderr)
        if initial:
            # A piped prompt cannot be edited in place, so the draft is shown and the line is read as usual.
            print(f"{DIM}draft: {initial}{RESET}", file=sys.stderr)
        line = input(f"{self.GLYPH} ")
        print()
        return line

    def _apply(self, key):
        # Returns the line on Enter, otherwise None. Keys that end the chat raise.
        if key == "ctrl-c":
            if not self.text:
                raise KeyboardInterrupt
            self.text, self.cursor = [], 0
            self._render()
            return None
        if key == "ctrl-d":
            if not self.text:
                raise EOFError
            del self.text[self.cursor:self.cursor + 1]
            self._render()
            return None
        if key == "eof":
            raise EOFError
        if key == "paste-start":
            self.pasting = True
            return None
        if key == "paste-end":
            self.pasting = False
            self._render()
            return None
        if self.pasting:
            self._insert("\n" if key == "enter" else key)
            if not self.keys.pending:   # one redraw for the whole paste, not one per character
                self._render()
            return None
        if key == "enter":
            line = "".join(self.text).strip()
            if not line:
                self._render()
                return None
            if not self.history or self.history[-1] != line:
                self.history.append(line)
            return line
        if key == "backspace":
            if self.cursor:
                del self.text[self.cursor - 1]
                self.cursor -= 1
        elif key in ("delete", "ctrl-k"):
            del self.text[self.cursor:self.cursor + (1 if key == "delete" else len(self.text))]
        elif key == "left":
            self.cursor = max(0, self.cursor - 1)
        elif key == "right":
            self.cursor = min(len(self.text), self.cursor + 1)
        elif key == "up":
            self._recall(-1)
        elif key == "down":
            self._recall(1)
        elif key in ("home", "ctrl-a"):
            self.cursor = 0
        elif key in ("end", "ctrl-e"):
            self.cursor = len(self.text)
        elif key == "ctrl-u":
            del self.text[:self.cursor]
            self.cursor = 0
        elif key == "ctrl-w":
            self._kill_word()
        elif key == "ctrl-l":
            _out(CLEAR)
            self.drawn = 0
        elif key == "tab":
            self._insert("    ")
        elif len(key) == 1:
            self._insert(key)
        self._render()
        return None

    def _insert(self, text):
        # A tab has no fixed width in a terminal, so it becomes spaces; other control characters
        # cannot be laid out at all and are dropped.
        clean = text.replace("\t", "    ")
        clean = "".join(c for c in clean if c == "\n" or c >= " ")
        self.text[self.cursor:self.cursor] = list(clean)
        self.cursor += len(clean)

    def _kill_word(self):
        end = start = self.cursor
        while start > 0 and self.text[start - 1].isspace():
            start -= 1
        while start > 0 and not self.text[start - 1].isspace():
            start -= 1
        del self.text[start:end]
        self.cursor = start

    def _recall(self, step):
        if not self.history:
            return
        self.hist = max(0, min(len(self.history), self.hist + step))
        self.text = list(self.history[self.hist]) if self.hist < len(self.history) else []
        self.cursor = len(self.text)

    def _wrap(self, room):
        # The typed text as rows that fit the room between the borders, and where the cursor sits.
        # Widths are carried along as each row is built, so a long line stays cheap to lay out.
        room = max(8, room)
        rows, widths = [[]], [0]
        cursor_row = cursor_col = 0
        for index, c in enumerate(self.text):
            if index == self.cursor:
                cursor_row, cursor_col = len(rows) - 1, widths[-1]
            if c == "\n":
                rows.append([])
                widths.append(0)
                continue
            width = cell_width(c)
            if widths[-1] + width > room:
                if c == " ":
                    continue      # a space that would overflow is dropped, not carried to the next row
                rows.append([])
                widths.append(0)
            rows[-1].append(c)
            widths[-1] += width
        if self.cursor >= len(self.text):
            cursor_row, cursor_col = len(rows) - 1, widths[-1]
        return ["".join(row) for row in rows], cursor_row, cursor_col

    def _border(self, columns, left, right, plain, styled):
        # A border row: one cap, the label, a fill to the far edge, then the other cap.
        fill = columns - 2 - cell_width(plain)
        return f"{DIM}{left}{RESET}{styled}{DIM}{'─' * max(0, fill)}{right}{RESET}\n"

    def _render(self):
        size = term_size()
        columns = max(24, size.columns)
        text_room = columns - 2 * self.EDGE - self.PREFIX
        rows, cursor_row, cursor_col = self._wrap(text_room)
        # A long message scrolls inside the box rather than pushing the transcript off the screen.
        room = max(1, size.lines - 6)
        offset = 0
        if len(rows) > room:
            offset = min(max(0, cursor_row - room + 1), len(rows) - room)
            rows = rows[offset:offset + room]
            cursor_row -= offset
        status_plain, status_styled = self.status_fn("".join(self.text), max(12, columns - 6))
        title = clip(self.label, max(4, columns - 14)) if self.label else ""
        if title:
            head_plain = f"─ {title} "
            head_styled = f"{DIM}─ {RESET}{paint(self.colour, title)}{DIM} "
        else:
            head_plain, head_styled = "─ ", f"{DIM}─ {RESET}"
        foot_plain = f"─ {status_plain} " if status_plain else "─ "
        foot_styled = f"{DIM}─ {RESET}{status_styled}{DIM} " if status_plain else f"{DIM}─ {RESET}"
        # The old box is lifted and the new one drawn in a single write, so no blank frame is shown between.
        out = [HIDE_CURSOR, SYNC_START, self._erase_code(), self._border(columns, "╭", "╮", head_plain, head_styled)]
        for i, row in enumerate(rows):
            if i == 0 and offset:
                glyph = f"{DIM}↑ {RESET}"
            elif i == 0:
                glyph = f"{paint(self.colour, self.GLYPH)}{BOLD}{RESET} " if self.colour else f"{GREEN}{BOLD}{self.GLYPH}{RESET} "
            else:
                glyph = " " * self.PREFIX
            pad = " " * max(0, text_room - cell_width(row))
            out.append(f"{DIM}│{RESET} {glyph}{row}{pad} {DIM}│{RESET}\n")
        out.append(self._border(columns, "╰", "╯", foot_plain, foot_styled))
        self.drawn = len(rows) + 2
        self.cursor_row = 1 + cursor_row          # the typed row, counted from the top border
        up = self.drawn - self.cursor_row         # from the row under the box back to the typed row
        if up > 0:
            out.append(f"\x1b[{up}A")
        out.append(f"\r\x1b[{self.EDGE + self.PREFIX + cursor_col}C")
        out.append(SHOW_CURSOR + SYNC_END)
        _out("".join(out))

    def _erase_code(self):
        # The codes that lift any drawing of the box, leaving the cursor where the transcript continues.
        if not self.drawn:
            return ""
        below = self.drawn - self.cursor_row   # the typed row down to the row under the box
        down = f"\x1b[{below}B" if below > 0 else ""
        return f"{down}\r\x1b[{self.drawn}A\x1b[J"

    def _erase(self):
        if not self.drawn:
            return
        _out(self._erase_code())
        self.drawn = 0


class Session:
    def __init__(self, client, show_images=True):
        self.client = client
        self.show_images = show_images   # False with --no-image: no picture when a chat opens (/portrait still works)
        self.solo, self.groups, self.personas = [], [], []
        self.colours = {}        # display name -> #rrggbb
        self.persona = None
        self.user_colour = None
        self.user_name = ""
        self.username = ""
        self.portrait_warned = False
        self.usage = None          # the last usage summary the server sent (see _usage_summary)
        self.pinned_model = ""
        self.model = ""            # the model that last answered this chat
        self.estimate = None       # the server's estimate of the next prompt (see /api/context)
        self.reply_length = None   # this account's reply length target in tokens; None = the character decides
        self.reply_bounds = (None, None)   # the admin's allowed range for that target
        self.usage_bar = self._saved_usage_bar()   # which token window the status bar shows
        self.composer = None       # the prompt at the bottom, while a chat is open
        self.refresh_me()

    @staticmethod
    def _saved_usage_bar():
        # The window picked with /settings-usage-bar, kept in the local settings file.
        saved = str(load_settings().get("usage_bar") or "").lower()
        return saved if saved in USAGE_BAR_KINDS else DEFAULT_USAGE_BAR

    def refresh_me(self):
        me = self.client.me()
        self.username = me["username"]
        self.persona = (me.get("persona") or {}).get("name")
        self.user_colour = (me.get("persona") or {}).get("color")
        self.user_name = self.persona or self.username
        self.usage = me.get("usage") or self.usage
        self.pinned_model = me.get("model") or ""
        self.reply_length = me.get("reply_length")
        bounds = me.get("reply_length_bounds") or [None, None]
        self.reply_bounds = (bounds[0], bounds[1]) if len(bounds) >= 2 else (None, None)
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
        colour = self.user_colour if role == "user" else self.colour_of(speaker)
        message = Message(speaker, colour)
        message.write_text(text)
        message.close()

    def refresh_estimate(self, character):
        # The server's picture of the next prompt: the system prompt and the messages the rolling
        # window will carry. One read-only call; on failure the status row simply leaves the figure out.
        try:
            self.estimate = self.client.context(character)
        except (ApiError, OSError):
            self.estimate = None

    def status_line(self, typed="", columns=FALLBACK_WIDTH):
        # The row under the prompt, in the manner of a coding agent's footer: the model that answers,
        # the clock, the token window the user chose for the bar (daily unless set otherwise), and the
        # estimated size of the next prompt. The richest line that fits the window is used; the clock
        # goes first, then the window name, then the remaining figure. Colours follow the web app:
        # green, amber near the limit, red past it. Returns (plain, styled): the composer needs the
        # plain text to measure the border row it sits in.
        meter = usage_meter(self.usage, self.usage_bar)
        estimate = prompt_estimate(self.estimate, typed)
        model = clip(self.model or self.pinned_model or "auto", 28)
        clock = time.strftime("%H:%M")
        estimate_label = "" if estimate is None else f"~{short_number(estimate)} next"

        def build(bar_cells, show_clock, show_kind, show_left, show_bar):
            parts = [(model, f"{BOLD}{model}{RESET}")]
            if show_clock:
                parts.append((clock, f"{DIM}{clock}{RESET}"))
            if meter and show_bar and bar_cells:
                kind, used, limit, share = meter
                filled = int(round(min(max(share, 0.0), 1.0) * bar_cells))
                if share > 0 and filled == 0:
                    filled = 1
                colour = GREEN if share < 0.7 else YELLOW if share < 0.9 else RED
                bar = "█" * filled + "░" * (bar_cells - filled)
                parts.append((bar, f"{colour}{'█' * filled}{DIM}{'░' * (bar_cells - filled)}{RESET}"))
                if show_left:
                    left = max(0, limit - used)
                    label = f"{short_number(left)} left" + (f" {kind}" if show_kind else "")
                    parts.append((label, f"{DIM}{label}{RESET}"))
            if estimate_label:
                parts.append((estimate_label, f"{DIM}{estimate_label}{RESET}"))
            plain = " · ".join(text for text, _ in parts)
            return plain, f"{DIM} · {RESET}".join(styled for _, styled in parts)

        cells = 10 if columns >= 76 else 6
        tries = [(cells, True, True, True, True), (cells, True, False, True, True),
                 (cells, False, False, True, True), (6, False, False, False, True)]
        if not meter:
            tries = [(0, True, False, False, False), (0, False, False, False, False)]
        for cells_n, show_clock, show_kind, show_left, show_bar in tries:
            plain, styled = build(cells_n, show_clock, show_kind, show_left, show_bar)
            if cell_width(plain) <= columns:
                return plain, styled
        fallback = estimate_label or model
        return fallback, f"{DIM}{fallback}{RESET}"

    def open_chat(self, character):
        # What the window holds when a chat opens: the character's picture, then the tail of the chat.
        self.model = ""            # the last answer came from another chat, so wait for this one's reply
        if self.show_images:
            self.show_portrait(character, from_open=True)
        group = next((g for g in self.groups if g["name"] == character), None)
        if group:
            heading = f"{BOLD}{character}{RESET} {DIM}· {', '.join(group.get('members', []))}"
        else:
            heading = f"{BOLD}{paint(self.colour_of(character), character)}{RESET}"
        print(f"{heading} {DIM}· you are {self.user_name} · /help for commands{RESET}")
        self.refresh_estimate(character)
        msgs = self.client.history(character)
        if not msgs:
            print(f"{DIM}(start of the chat){RESET}\n")
        else:
            print(f"{DIM}last {min(2, len(msgs))} of {len(msgs)} messages{RESET}")
            for m in msgs[-2:]:
                self.print_message(m["speaker"], m["text"], m["role"])

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

    def choose_character(self, current):
        # The in-chat switcher: the numbered list, then one answer. Returns the new name, or None to stay.
        self.list_characters()
        raw = input("Switch to (number or name, blank to stay): ").strip()
        if not raw:
            return None
        name = self.resolve(raw)
        if name is None:
            print("No such character. /characters lists them.")
            return None
        if name == current:
            print(f"Already chatting with {name}.")
            return None
        return name

    def show_usage(self):
        # Requests and every token window, read fresh from the server so the figures are current.
        try:
            me = self.refresh_me()
        except (ApiError, OSError) as err:
            print(f"{RED}! {err}{RESET}")
            return
        usage = me.get("usage") or {}
        budgets = usage.get("token_budgets") or {}
        meter = usage_meter(usage, self.usage_bar)
        # The bar follows the chosen window; mark the window it actually falls back to, if it had no limit.
        bar_kind = meter[0] if meter else self.usage_bar
        print(f"{BOLD}Usage{RESET} {DIM}(tier {budgets.get('tier') or 'unknown'}, bar shows {bar_kind}){RESET}")
        cap = usage.get("request_cap")
        requests_today = usage.get("requests_today", 0)
        print(f"  Requests today  {requests_today}" + (f" of {cap}" if cap else ""))
        tokens = usage.get("tokens_today", 0)
        display_limit = usage.get("token_display_limit")
        print(f"  Tokens today    {short_number(tokens)}" + (f" of {short_number(display_limit)}" if display_limit else ""))
        for kind in USAGE_BAR_KINDS:
            window = budgets.get(kind)
            if not isinstance(window, dict) or not window.get("limit"):
                continue
            used, limit = int(window.get("used", 0)), int(window["limit"])
            share = min(max(used / limit, 0.0), 1.0)
            cells = 12
            filled = int(round(share * cells))
            if share > 0 and filled == 0:
                filled = 1
            colour = GREEN if share < 0.7 else YELLOW if share < 0.9 else RED
            bar = f"{colour}{'█' * filled}{DIM}{'░' * (cells - filled)}{RESET}"
            reset = window.get("reset") or ""
            mark = "  <- bar" if kind == bar_kind else ""
            print(f"  {kind.title():<7} {bar} {short_number(used)} of {short_number(limit)} {DIM}({reset}){RESET}{mark}")
        lo, hi = self.reply_bounds
        length = f"{self.reply_length} tokens" if self.reply_length else "automatic"
        span = f", allowed {lo} to {hi}" if lo and hi else ""
        print(f"  Reply length    {length} {DIM}(/message-length{span}){RESET}")

    def set_message_length(self, arg):
        # The reply length target is an account setting, so it holds for every chat.
        try:
            me = self.refresh_me()
        except (ApiError, OSError) as err:
            print(f"{RED}! {err}{RESET}")
            return
        lo, hi = self.reply_bounds
        if not arg:
            shown = f"{self.reply_length} tokens" if self.reply_length else "automatic (the character decides)"
            print(f"Reply length target: {shown}.")
            if lo and hi:
                print(f"{DIM}Set it with /message-length <tokens>, from {lo} to {hi}. /message-length auto clears it.{RESET}")
            return
        if arg.lower() in ("auto", "off", "default", "none", "0"):
            value = ""
        else:
            try:
                value = int(arg)
            except ValueError:
                print("Give a whole number of tokens, or auto.")
                return
        try:
            res = self.client.set_settings({"reply_length": value})
        except (ApiError, OSError) as err:
            print(f"{RED}! {err}{RESET}")
            return
        self.reply_length = res.get("reply_length")
        if self.reply_length:
            if me.get("thinking"):
                print(f"Reply length target set to {self.reply_length} tokens, not counting reasoning.")
            else:
                print(f"Reply length target set to {self.reply_length} tokens.")
        else:
            print("Reply length target cleared; each reply follows the character's own length.")

    def set_usage_bar(self, arg):
        # Which of the three token windows the prompt's bar follows. A terminal-only preference, kept
        # in ~/.miso_remote/settings.json, so it applies to every account on this machine.
        if not arg:
            print(f"The prompt's usage bar shows the {BOLD}{self.usage_bar}{RESET} window.")
            print(f"{DIM}Choose with /settings-usage-bar daily, weekly or monthly.{RESET}")
            return
        choice = arg.strip().lower()
        if choice not in USAGE_BAR_KINDS:
            print(f"Pick one of: {', '.join(USAGE_BAR_KINDS)}.")
            return
        self.usage_bar = choice
        try:
            save_setting("usage_bar", choice)
        except OSError as err:
            print(f"{DIM}(could not save the setting: {err}){RESET}")
        print(f"The prompt's usage bar now shows the {choice} window.")

    def autofill(self, character):
        # Asks the server to draft the user's next message. The draft is returned to be put in the
        # prompt for editing; it is not saved to the chat and not sent.
        spinner = None
        if self.composer is not None and self.composer.interactive:
            spinner = Spinner(f"drafting a message for {character}")
            spinner.start()
        try:
            res = self.client.autofill(character)
        except ApiError as err:
            print(f"{RED}! {err}{RESET}")
            return ""
        except OSError as err:
            print(f"{RED}! The connection dropped ({err}).{RESET}")
            return ""
        finally:
            if spinner is not None:
                spinner.halt()
        draft = str(res.get("text") or "").strip()
        if not draft:
            print("The server sent no draft. Try again.")
            return ""
        if self.composer is not None and self.composer.interactive:
            print(f"{DIM}drafted a message; edit it in the prompt, or Ctrl+U to clear it{RESET}")
        return draft

    def add_palace_entry(self, character, arg=""):
        # One Mind Palace entry: title, trigger keys and text. Reads the current list first, because the
        # server saves the whole list, and keeps the palace's own settings as they were.
        try:
            data = self.client.palace(character)
        except (ApiError, OSError) as err:
            print(f"{RED}! {err}{RESET}")
            return
        entries = data.get("entries") or []
        print(f"{BOLD}Mind Palace{RESET} {DIM}({len(entries)} entries for {character}, budget "
              f"{short_number(data.get('budget'))} characters){RESET}")
        for i, entry in enumerate(entries, 1):
            keys = ", ".join(entry.get("keys") or [])
            if not keys:
                keys = "always on" if entry.get("constant") else "no keys"
            print(f"  {i:>2}. {entry.get('title') or '(untitled)'} {DIM}[{keys}]{RESET}")
        # One-line form: /memory-palace Title | key, key | the text. Otherwise each part is asked for.
        parts = [p.strip() for p in arg.split("|", 2)] if "|" in arg else []
        if parts and len(parts) != 3:
            print("Use /memory-palace Title | key, key | text, or /memory-palace on its own to be asked for each part.")
            return
        if parts:
            title, keys_text, content = parts
        else:
            title = (arg or input("Title (blank to cancel): ")).strip()
            if not title:
                return
            keys_text = input("Trigger keys (comma separated, blank for always on): ").strip()
            content = input("Entry text: ").strip()
        if not title:
            print("An entry needs a title. Nothing was saved.")
            return
        if not content:
            print("An entry needs text. Nothing was saved.")
            return
        keys = [k.strip() for k in keys_text.split(",") if k.strip()]
        entry = {"title": title, "keys": keys, "content": content, "constant": not keys}
        try:
            res = self.client.set_palace(character, entries + [entry], data.get("recursive"), data.get("budget"))
        except (ApiError, OSError) as err:
            print(f"{RED}! {err}{RESET}")
            return
        print(f"Saved '{title}' to {character}'s Mind Palace ({len(res.get('entries') or [])} entries).")

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
                ("/characters", "pick another character or group to chat with"),
                ("/autofill", "draft your next message and put it in the prompt"),
                ("/save", "refresh the memory bank now (server)"),
                ("/memory-palace", "add an entry to this character's or group's Mind Palace"),
                ("/usage", "requests and token budgets"),
                ("/settings-usage-bar [w]", "which window the prompt bar shows"),
                ("/message-length [n]", "show or set the reply length in tokens"),
                ("/model [name|auto]", "show or pin the model (server)"),
                ("/back or /undo", "remove the last exchange (server)"),
                ("/history", "print the whole chat"),
                ("/portrait", "show this character's picture"),
                ("/memory", "show the memory bank and pinned facts"),
                ("/persona", "pick the persona you play as"),
                ("/clear", "clear the screen"),
                ("/switch", "go back to the character list"),
                ("/exit", "log out and quit"),
            ]
        else:
            rows = [
                ("<number or name>", "open a character or group"),
                ("/characters", "list characters and groups"),
                ("/persona", "pick the persona you play as"),
                ("/group new", "make a private group from characters"),
                ("/usage", "requests and token budgets"),
                ("/settings-usage-bar [w]", "which window the prompt bar shows"),
                ("/message-length [n]", "show or set the reply length in tokens"),
                ("/clear", "clear the screen"),
                ("/exit", "log out and quit"),
            ]
        for cmd, desc in rows:
            print(f"  {cmd:<24} {desc}")
        if in_chat:
            print(f"{DIM}/memory-palace Title | key, key | text adds an entry in one line.{RESET}")
            print(f"{DIM}/settings-usage-bar takes daily, weekly or monthly; the default is daily.{RESET}")

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
        # Returns (action, target): "exit", "switch" back to the list, or "open" another character.
        self.open_chat(character)
        composer = Composer(self.status_line, label=character, colour=self.colour_of(character))
        self.composer = composer
        pending = ""      # text the next prompt opens with, as /autofill leaves its draft
        try:
            while True:
                try:
                    text = composer.read(pending)
                except EOFError:
                    return "exit", None
                pending = ""
                low = text.lower()
                if low in ("/exit", "/quit", "exit", "quit"):
                    return "exit", None
                if low == "/switch":
                    return "switch", None
                word, _, arg = text.partition(" ")
                word, arg = word.lower(), arg.strip()
                if word == "/help":
                    self.help(in_chat=True)
                elif word == "/autofill":
                    pending = self.autofill(character)
                    continue
                elif word == "/characters":
                    chosen = self.choose_character(character)
                    if chosen:
                        return "open", chosen
                elif word == "/usage":
                    self.show_usage()
                elif word == "/settings-usage-bar":
                    self.set_usage_bar(arg)
                elif word == "/message-length":
                    self.set_message_length(arg)
                elif word == "/memory-palace":
                    self.add_palace_entry(character, arg)
                elif word == "/history":
                    self.show_history(character)
                elif word == "/memory":
                    self.show_memory(character)
                elif word == "/persona":
                    self.pick_persona()
                elif word == "/portrait":
                    self.show_portrait(character)
                elif word in ("/save", "/back", "/undo", "/model", "/verbose"):
                    # Server commands: no message bubble, only the server's notice.
                    self.send(character, text, show_user=False)
                    continue
                elif word == "/clear":
                    _out(CLEAR)
                    continue
                else:
                    # Anything else, including an unknown slash word, is sent as a chat message.
                    self.send(character, text)
                    continue
                self.refresh_estimate(character)
        finally:
            self.composer = None

    def send(self, character, text, show_user=True):
        state = {"speaker": None, "writer": None, "shown": {}, "replies": [], "started": time.time(), "model": None}
        # The composer was lifted to get the line; put the message into the transcript in its place.
        # A server command is not a message, so show_user is off for those.
        if show_user:
            self.print_message(self.user_name, text, "user")
        spinner = None
        if self.composer is not None and self.composer.interactive:
            spinner = Spinner(f"waiting for {character}")
            spinner.start()

        def on_event(event, payload):
            if spinner is not None:
                spinner.halt()      # the reply is about to be drawn; the mark must be gone first
            if event == "delta":
                speaker, body = payload.get("speaker") or "", payload.get("text") or ""
                seen = state["shown"].get(speaker, "")
                if state["speaker"] != speaker:
                    if state["writer"]:
                        state["writer"].close()
                    state["speaker"], state["writer"] = speaker, Message(speaker, self.colour_of(speaker))
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
                        message = Message(r.get("speaker", ""), r.get("color") or self.colour_of(r.get("speaker", "")))
                        message.write_text(r.get("text", ""))
                        message.close()
                if state["replies"]:
                    state["model"] = state["replies"][-1].get("model")
                    if state["model"]:
                        self.model = state["model"]
                if payload.get("selector_note"):
                    print(f"{DIM}({payload['selector_note']}){RESET}")
            elif event == "usage":
                took = time.time() - state["started"]
                self.usage = payload
                print(f"{DIM}{took:.1f}s · requests {payload.get('requests_today')}/{payload.get('request_cap')} today{RESET}")
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
        finally:
            if spinner is not None:
                spinner.halt()
        # The turn is saved now, so the next prompt's window has moved on.
        self.refresh_estimate(character)

    def menu(self):
        self.list_characters()
        print()
        while True:
            try:
                raw = input("menu> ").strip()
            except EOFError:
                return None
            low = raw.lower()
            word, _, arg = raw.partition(" ")
            word, arg = word.lower(), arg.strip()
            if not raw:
                continue
            if low in ("/exit", "/quit", "exit", "quit"):
                return None
            if word == "/help":
                self.help(in_chat=False)
            elif word == "/characters":
                self.list_characters()
            elif word == "/persona":
                self.pick_persona()
            elif word == "/usage":
                self.show_usage()
            elif word == "/settings-usage-bar":
                self.set_usage_bar(arg)
            elif word == "/message-length":
                self.set_message_length(arg)
            elif word == "/group new":
                made = self.new_group()
                if made:
                    return made
            elif word == "/clear":
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
    parser.add_argument("--no-image", action="store_true", help="don't show the character's picture when a chat opens")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    base = args.server.rstrip("/")
    host = urllib.parse.urlparse(base).hostname or ""
    if not base.startswith("https://") and host not in ("127.0.0.1", "localhost"):
        raise SystemExit("Use an https:// address for a remote server.")

    client = Client(base)
    client.check_version()
    username = args.username or input("Username: ").strip()
    password = ask_password("Password: ")
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

    session = Session(client, show_images=not args.no_image)
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
        character = None
        while True:
            if wanted:
                character = session.resolve(wanted)
                if character is None:
                    raise SystemExit(f"No character or group called {wanted!r} on this server.")
                wanted = None
            elif character is None:
                character = session.menu()
            if character is None:
                break
            action, target = session.chat(character)
            if action == "exit":
                break
            # "open" moves straight to the character picked in the chat; "switch" goes back to the list.
            character = target if action == "open" else None
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
