"""Theme music: up to three tracks for a character or an account profile.

A track is kept exactly as uploaded when it is already a browser format at 160 kbps or less, so it is never converted
twice. Anything else is converted once on upload: Opus in WebM at 160 kbps. Opus is not played by every Safari, so an
Opus track also keeps an AAC copy in M4A at 192 kbps, and the page tries the Opus copy first and the AAC copy second.
"""
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
from urllib.parse import quote

from web import editor

MAX_TRACKS = 3
UPLOAD_MAX = 60 * 1024 * 1024        # the largest upload accepted, before conversion
MAX_SECONDS = 15 * 60                # a longer track takes too long to convert in one request
TITLE_MAX = 60
MUSIC_DIR = "music"
INDEX = "tracks.json"
KEEP_BPS = 160_000                   # a browser-format source at or below this is kept as uploaded
OPUS_BPS = "160k"
AAC_BPS = "192k"
TRACK_ID_RE = re.compile(r"^[0-9a-f]{12}$")
FILE_RE = re.compile(r"^[0-9a-f]{12}\.(webm|ogg|m4a|mp3)$")
# The type a browser is told, with the codec where it matters, so it can skip a copy it cannot play.
MIME = {
    ".webm": 'audio/webm; codecs="opus"',
    ".ogg": 'audio/ogg; codecs="opus"',
    ".m4a": 'audio/mp4; codecs="mp4a.40.2"',
    ".mp3": "audio/mpeg",
}
# The plain type the file is served as.
SERVED = {".webm": "audio/webm", ".ogg": "audio/ogg", ".m4a": "audio/mp4", ".mp3": "audio/mpeg"}


class MusicError(ValueError):
    pass


def _tools():
    # ffmpeg and ffprobe, or a refusal. Music is the second feature that needs them, after video backgrounds.
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise MusicError("Theme music needs ffmpeg on this server. Install ffmpeg, then try again.")
    return ffmpeg, ffprobe


def _dir(folder):
    return os.path.join(folder, MUSIC_DIR)


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _probe(ffprobe, path):
    # The first audio stream's codec and bit rate, the container's name and length, and whether the file also has a
    # picture or video stream (cover art counts, so such a file is converted, which drops the picture).
    proc = subprocess.run([ffprobe, "-v", "error", "-show_entries",
                           "stream=codec_type,codec_name,bit_rate:format=format_name,duration,bit_rate",
                           "-of", "json", path], capture_output=True, timeout=60)
    if proc.returncode != 0:
        raise MusicError("Could not read that audio file. Use an MP3, M4A, AAC, Opus, WebM, Ogg or WAV file.")
    try:
        info = json.loads(proc.stdout.decode("utf-8", "replace") or "{}")
    except ValueError:
        raise MusicError("Could not read that audio file.") from None
    streams = info.get("streams") or []
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    if not audio:
        raise MusicError("That file has no sound in it.")
    fmt = info.get("format") or {}
    stream = audio[0]
    return {
        "codec": str(stream.get("codec_name") or ""),
        "formats": str(fmt.get("format_name") or "").split(","),
        "bitrate": _num(stream.get("bit_rate")) or _num(fmt.get("bit_rate")),
        "duration": _num(fmt.get("duration")),
        "video": any(s.get("codec_type") == "video" for s in streams),
    }


def _kept_ext(info):
    # The extension of a source that is kept as uploaded, or None when it must be converted. Only a browser format
    # at KEEP_BPS or lower is kept, and only with no picture in it.
    if info["video"] or not info["bitrate"] or info["bitrate"] > KEEP_BPS:
        return None
    formats, codec = info["formats"], info["codec"]
    if codec == "mp3" and formats == ["mp3"]:
        return ".mp3"
    if codec == "aac" and ("mp4" in formats or "m4a" in formats):
        return ".m4a"
    if codec == "opus" and "webm" in formats:
        return ".webm"
    if codec == "opus" and "ogg" in formats:
        return ".ogg"
    return None


def _read_file(path):
    with open(path, "rb") as f:
        return f.read()


def _run(ffmpeg, args, source):
    # One ffmpeg conversion of the audio in source. Any failure is refused in plain words.
    proc = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", source,
                           "-vn", "-sn", "-dn", *args], capture_output=True, timeout=600)
    if proc.returncode != 0:
        raise MusicError("That track could not be converted.")


def _aac(ffmpeg, source, work):
    # The AAC copy in M4A, for browsers that cannot play Opus.
    out = os.path.join(work, "copy.m4a")
    _run(ffmpeg, ["-map", "0:a:0", "-c:a", "aac", "-b:a", AAC_BPS, "-movflags", "+faststart",
                  "-f", "mp4", out], source)
    return _read_file(out)


def _convert(ffmpeg, source, work):
    # Opus in WebM first, then the AAC copy. Returns the copies in the order the page should try them.
    out = os.path.join(work, "main.webm")
    _run(ffmpeg, ["-map", "0:a:0", "-c:a", "libopus", "-b:a", OPUS_BPS, "-vbr", "on",
                  "-application", "audio", "-f", "webm", out], source)
    return {".webm": _read_file(out), ".m4a": _aac(ffmpeg, source, work)}


def _write(path, payload):
    # Written beside its final name and moved into place, so a half-written file is never served.
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        f.write(payload)
    os.replace(tmp, path)


def _read_index(folder):
    # The tracks in order, each with only the files that are still on disk.
    try:
        with open(os.path.join(_dir(folder), INDEX), encoding="utf-8") as f:
            items = json.load(f)
    except (OSError, ValueError):
        return []
    if not isinstance(items, list):
        return []
    out = []
    for item in items:
        if not isinstance(item, dict) or not TRACK_ID_RE.match(str(item.get("id", ""))):
            continue
        files = [n for n in item.get("files") or [] if FILE_RE.match(str(n))
                 and os.path.isfile(os.path.join(_dir(folder), n))]
        if files:
            out.append({"id": item["id"], "title": str(item.get("title") or ""),
                        "duration": _num(item.get("duration")) or 0, "files": files})
    return out


def _write_index(folder, tracks):
    d = _dir(folder)
    os.makedirs(d, exist_ok=True)
    _write(os.path.join(d, INDEX), (json.dumps(tracks, indent=1) + "\n").encode("utf-8"))


def clean_title(text, fallback):
    # One line of plain text, without control characters, at most TITLE_MAX characters.
    line = re.sub(r"[\x00-\x1f\x7f]+", " ", str(text or ""))
    line = re.sub(r"\s+", " ", line)[:TITLE_MAX].strip()
    return line or fallback


def tracks(folder):
    # The tracks in playing order. Empty when there are none.
    return _read_index(folder)


def add(folder, data, title=""):
    # Adds one track and returns its entry. A source that is kept is stored byte for byte; a converted one is stored as
    # Opus in WebM with the AAC copy beside it. Refuses past MAX_TRACKS, before any conversion runs.
    if not data:
        raise MusicError("Choose an audio file first.")
    if len(data) > UPLOAD_MAX:
        raise MusicError(f"A track can be up to {UPLOAD_MAX // (1024 * 1024)} MB.")
    current = _read_index(folder)
    if len(current) >= MAX_TRACKS:
        raise MusicError(f"A list can have up to {MAX_TRACKS} tracks. Remove one first.")
    ffmpeg, ffprobe = _tools()
    work = tempfile.mkdtemp(prefix="miso-music-")
    try:
        source = os.path.join(work, "source")
        with open(source, "wb") as f:
            f.write(data)
        info = _probe(ffprobe, source)
        duration = info["duration"]
        if not duration or duration <= 0:
            raise MusicError("Could not read how long that track is.")
        if duration > MAX_SECONDS:
            raise MusicError(f"A track can be up to {MAX_SECONDS // 60} minutes; this one is "
                             f"{duration / 60:.0f} minutes.")
        # Some containers do not state a bit rate. The average over the length is close enough to judge by.
        info["bitrate"] = info["bitrate"] or len(data) * 8 / duration
        keep = _kept_ext(info)
        if keep is None:
            outputs = _convert(ffmpeg, source, work)
        else:
            outputs = {keep: data}
            # An Opus file is kept, but Safari may not play it, so it gets the AAC copy too.
            if keep in (".webm", ".ogg"):
                outputs[".m4a"] = _aac(ffmpeg, source, work)
        taken = {t["id"] for t in current}
        tid = secrets.token_hex(6)
        while tid in taken:
            tid = secrets.token_hex(6)
        names = []
        for ext, payload in outputs.items():
            name = tid + ext
            os.makedirs(_dir(folder), exist_ok=True)
            _write(os.path.join(_dir(folder), name), payload)
            names.append(name)
        entry = {"id": tid, "title": clean_title(title, "Track %d" % (len(current) + 1)),
                 "duration": round(duration, 1), "files": names}
        _write_index(folder, [{**t} for t in current] + [entry])
        return entry
    except subprocess.TimeoutExpired:
        raise MusicError("That track took too long to convert. Try a shorter one.") from None
    finally:
        shutil.rmtree(work, ignore_errors=True)


def remove(folder, tid):
    current = _read_index(folder)
    hit = [t for t in current if t["id"] == tid]
    if not hit:
        raise MusicError("No such track.")
    for name in hit[0]["files"]:
        path = os.path.join(_dir(folder), name)
        if os.path.isfile(path):
            os.remove(path)
    _write_index(folder, [t for t in current if t["id"] != tid])


def reorder(folder, ids):
    # Puts the tracks in the order given. The list must name each track exactly once.
    current = _read_index(folder)
    by_id = {t["id"]: t for t in current}
    if not isinstance(ids, list) or sorted(str(i) for i in ids) != sorted(by_id):
        raise MusicError("The order must name each track once.")
    _write_index(folder, [by_id[i] for i in ids])


def file_path(folder, fname):
    # The path of one stored copy, or None. The name is checked before it touches the disk.
    if not FILE_RE.match(str(fname or "")):
        return None
    p = os.path.join(_dir(folder), fname)
    return p if os.path.isfile(p) else None


def public(folder, base):
    # The tracks as the page reads them: id, title, length, and the copies to try in order, each with its URL and type.
    return [{"id": t["id"], "title": t["title"], "duration": t["duration"],
             "sources": [{"src": base + n, "type": MIME[os.path.splitext(n)[1]]} for n in t["files"]]}
            for t in _read_index(folder)]


def character_tracks(root, name):
    # The tracks of a character, for its card and its chat. Empty for a name that is not a character.
    try:
        folder = editor.folder_of(root, name)
    except editor.EditorError:
        return []
    return public(folder, "/api/character-music/%s/file/" % quote(name, safe=""))


def account_tracks(folder, username):
    # The tracks of an account's profile page. The folder is the account's own profile folder.
    return public(folder, "/api/account-music/%s/file/" % quote(username, safe=""))
