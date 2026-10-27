# Character editor for the web app: create, read and update character folders, and manage icons.
#
# Admin only. A character is a folder under the character root with character.md (persona, with a
# 'Colour:' line), optional context.md (memories, locations, situations) and optional icon.png.
# Names are restricted to one folder level so nothing can be written outside the character root.
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import threading

from PIL import Image, ImageOps

import sandbox
from charname import check_display, folder_for
from web import profiles, examples

COLOUR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
UPLOAD_MAX = 10 * 1024 * 1024         # largest file an admin may upload
ICON_OUT_MAX = 512 * 1024             # the stored icon stays under this
GIF_MAX_BYTES = 8 * 1024 * 1024       # an animated GIF is kept as uploaded, so it has its own cap
GIF_MAX_FRAMES = 400                  # a longer loop is almost always a video converted by mistake
# A character's picture, in the order they are looked for. The last one is an animated GIF, stored as
# uploaded because re-encoding it would flatten it to a single frame.
ICON_FILES = ("icon.png", "icon.jpg", "icon.webp", "icon.gif")
ICON_SIDES = (512, 384, 320, 256, 192, 128, 96)   # tried largest first; the first that fits is kept
MAX_SOURCE_PIXELS = 40_000_000        # refuse huge images before decoding them
EXAMPLE_CARD = """Character Persona: Example Fox
Colour: #e8a33d

- Species: A lean fox anthro with a crimson scarf and a brass pocket watch.
- Personality: Curious and warm, teases gently, hates being rushed.
- Appearance: Amber fur, cream muzzle, green eyes, scarf always tied.
- Setting: A rainy harbour town; the lighthouse keeper is an old friend.
- Speech: Short sentences, fond nicknames, the occasional *tail flick*.
"""
# The example messages that go with the sample card, so the editor's example section shows what it is for.
EXAMPLE_MESSAGES = [
    {"user": "You're late again, fox.",
     "character": "*one ear tips toward you, unbothered* The tide was late first. I only followed it."},
    {"user": "What did you find down at the harbour?",
     "character": "A brass cog with no machine left to want it. *turns it over in her palm* Yours, if you like."},
]


class EditorError(ValueError):
    pass


def valid_name(name):
    # A display name as the admin types it. The folder name is derived from it (charname.folder_for).
    try:
        return check_display(name)
    except ValueError as err:
        raise EditorError(str(err)) from None


def folder_of(root, name):
    name = valid_name(name)
    path = os.path.join(root, folder_for(name))
    if os.path.dirname(os.path.realpath(path)) != os.path.realpath(root):
        raise EditorError("That name points outside the character folder.")
    return path


def read_card(root, name):
    path = folder_of(root, name)
    if not os.path.isfile(os.path.join(path, "character.md")):
        raise EditorError("No such character.")
    with open(os.path.join(path, "character.md"), encoding="utf-8", errors="replace") as f:
        card = f.read()
    context = ""
    if os.path.isfile(os.path.join(path, "context.md")):
        with open(os.path.join(path, "context.md"), encoding="utf-8", errors="replace") as f:
            context = f.read()
    m = re.search(r"^\s*[-*]?\s*colou?r\s*:\s*(.+)$", card, re.I | re.M)
    colour = m.group(1).split("/")[0].strip() if m else ""
    return {"name": name, "card": card, "context": context, "colour": colour,
            "has_icon": icon_path(root, name) is not None, "backgrounds": background_ids(root, name), "sandbox": sandbox.is_enabled(path),
            "profile": profiles.read_profile(path), "examples": examples.load(path)}


def write_card(root, name, card, context, colour_hex, creating, sandbox_on=None, profile=None, example_messages=None):
    # profile is the card's headline, info and tags. Blank headline and info leave the saved profile
    # alone, so the background writer can still fill it in later. example_messages is None when the caller does not
    # manage them, which leaves the saved list as it is; a list replaces it (an empty list removes the file).
    path = folder_of(root, name)
    if creating and os.path.exists(path):
        raise EditorError("A character with that name already exists.")
    if not creating and not os.path.isfile(os.path.join(path, "character.md")):
        raise EditorError("No such character.")
    if not isinstance(card, str) or not card.strip():
        raise EditorError("The character card can't be empty.")
    if len(card) > 20000 or len(context or "") > 20000:
        raise EditorError("Keep the card and context under 20,000 characters each.")
    new_profile = _checked_profile(profile)   # checked before anything is written
    # Checked before anything is written as well, so a bad example never leaves a half-saved card.
    try:
        new_examples = None if example_messages is None else examples.validate(example_messages)
    except examples.ExamplesError as err:
        raise EditorError(str(err)) from None
    colour_hex = (colour_hex or "").strip()
    if COLOUR_RE.match(colour_hex):
        card = _set_colour_line(card, colour_hex.lower())
    elif creating:
        raise EditorError("Colour must be a hex code such as #c0392b.")
    # on an existing character, a colour name such as 'Crimson' is kept as it is in the card
    os.makedirs(path, exist_ok=True)
    _atomic_write(os.path.join(path, "character.md"), card)
    if sandbox_on is not None:      # None leaves the flag as it is
        sandbox.set_enabled(path, sandbox_on)
    context_path = os.path.join(path, "context.md")
    if (context or "").strip():
        _atomic_write(context_path, context.strip() + "\n")
    elif os.path.exists(context_path):
        os.remove(context_path)
    if new_profile is not None:
        profiles.save_profile(path, new_profile, auto=False)
    if new_examples is not None:
        examples.save(path, new_examples)
    return read_card(root, name)


def _checked_profile(profile):
    # None when the admin left the profile blank; otherwise the cleaned profile or an EditorError.
    if not isinstance(profile, dict):
        return None
    headline = str(profile.get("headline") or "")
    info = str(profile.get("info") or "")
    if not headline.strip() and not info.strip():
        return None
    try:
        return profiles.clean_profile(headline, info, profile.get("tags"))
    except profiles.ProfileError as err:
        raise EditorError(str(err)) from None


def _set_colour_line(card, colour_hex):
    # Keep one Colour: line in the card, set to the hex the bubbles use.
    lines = card.replace("\r\n", "\n").split("\n")
    for i, line in enumerate(lines):
        if re.match(r"^\s*[-*]?\s*colou?r\s*:", line, re.I):
            lines[i] = "Colour: " + colour_hex
            return "\n".join(lines)
    return "Colour: " + colour_hex + "\n" + "\n".join(lines)


def _atomic_write(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


CREATOR_FILE = "creator.json"
DEFAULT_CREATOR = "admin"   # folders from before creator tracking have no file


def creator_of(path):
    # Username that made the character. Missing or unreadable means the default.
    try:
        with open(os.path.join(path, CREATOR_FILE), encoding="utf-8") as f:
            name = json.load(f).get("creator")
    except (OSError, ValueError, AttributeError):
        return DEFAULT_CREATOR
    return name if isinstance(name, str) and name.strip() else DEFAULT_CREATOR


def record_creator(path, username):
    # Written once at creation, so later edits never change who made the character.
    target = os.path.join(path, CREATOR_FILE)
    if os.path.exists(target):
        return
    _atomic_write(target, json.dumps({"creator": username}) + "\n")


BG_LEGACY = "background.jpg"         # the single background from before several were allowed; still served
# A picture background is stored as PNG. Pictures saved before that are JPEG and are still read. An animated GIF
# is kept as uploaded, because re-encoding it would flatten the loop to one frame.
BG_IMAGE_EXT = ".png"
BG_IMAGE_EXTS = (".png", ".jpg", ".gif")
BG_DIR = "backgrounds"               # one file per background, named by its id
BG_MAX_SIDE = 1280                   # chat and card backgrounds are downsized to this
MAX_BACKGROUNDS = 10                 # per character
LEGACY_ID = "legacy"
BG_ID_RE = re.compile(r"^[0-9a-f]{12}$")
# A background is a picture, or a video. A video id carries .webm so the page can tell the two apart without
# another request, and its poster frame sits beside it as <id>.poster.jpg. Every video is stored twice: VP9 in
# WebM, which is small, and H.264 in MP4, which every phone can play. The page offers both and the browser picks.
VIDEO_STORE_EXT = ".webm"
VIDEO_FALLBACK_EXT = ".mp4"
VIDEO_POSTER_SUFFIX = ".poster.jpg"
VIDEO_MAX_BYTES = 50 * 1024 * 1024   # a background video upload may be up to 50 MB
VIDEO_MIN_SIDE = 720                 # and at least 720p on its shorter side
VIDEO_MAX_SECONDS = 60               # a longer clip takes too long to transcode for one request
VIDEO_CRF = 32                       # VP9 constant quality; a background sits behind text, so 32 is plenty
VIDEO_FALLBACK_CRF = 26              # H.264 quality for the MP4 copy phones play; closer to the eye than VP9 32
# The blur and dimming a background sits under are baked in here, once, so the page never filters the video
# live. The sigma is in video pixels and roughly matches the 5px blur the page used to apply on screen.
VIDEO_BLUR_SIGMA = 5
# The colour look the page used to apply live to every picture and video: sepia, then saturation, then dimming
# (CSS applies filter functions in that order). It is folded into one 3x3 colour matrix, so each stored copy gets
# it once and the page keeps only the contrast step.
LOOK_SEPIA = 0.16
LOOK_SATURATE = 1.08
LOOK_BRIGHTNESS = 0.95


def _matmul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _look_matrix():
    s = LOOK_SEPIA
    sepia = [[0.393 + 0.607 * (1 - s), 0.769 - 0.769 * (1 - s), 0.189 - 0.189 * (1 - s)],
             [0.349 - 0.349 * (1 - s), 0.686 + 0.314 * (1 - s), 0.168 - 0.168 * (1 - s)],
             [0.272 - 0.272 * (1 - s), 0.534 - 0.534 * (1 - s), 0.131 + 0.869 * (1 - s)]]
    k = LOOK_SATURATE
    sat = [[0.213 + 0.787 * k, 0.715 - 0.715 * k, 0.072 - 0.072 * k],
           [0.213 - 0.213 * k, 0.715 + 0.285 * k, 0.072 - 0.072 * k],
           [0.213 - 0.213 * k, 0.715 - 0.715 * k, 0.072 + 0.928 * k]]
    b = LOOK_BRIGHTNESS
    dim = [[b, 0, 0], [0, b, 0], [0, 0, b]]
    return _matmul(dim, _matmul(sat, sepia))


LOOK_MATRIX = _look_matrix()
# Pillow's convert takes 12 numbers: each output channel's three input weights, then an offset (none here).
PIL_LOOK = tuple(x for row in LOOK_MATRIX for x in (*row, 0.0))
# ffmpeg's colorchannelmixer takes the same weights row by row, each row followed by an alpha term (unused).
FFMPEG_LOOK = "colorchannelmixer=" + ":".join(f"{x:.5f}" for row in LOOK_MATRIX for x in (*row, 0))
VIDEO_LOOK = f"gblur=sigma={VIDEO_BLUR_SIGMA},format=gbrp,{FFMPEG_LOOK}"
BG_ANY_ID_RE = re.compile(r"^[0-9a-f]{12}(\.webm|\.mp4)?$")
POSTER_ID_RE = re.compile(r"^[0-9a-f]{12}\.poster\.jpg$")   # the still frame of a video background


def is_video_id(bid):
    # True when a background id names a video rather than an image.
    return str(bid or "").lower().endswith((VIDEO_STORE_EXT, VIDEO_FALLBACK_EXT))


def _video_stem(bid):
    # The id without its video extension, so .webm and .mp4 copies share one poster.
    low = str(bid or "").lower()
    for ext in (VIDEO_STORE_EXT, VIDEO_FALLBACK_EXT):
        if low.endswith(ext):
            return str(bid)[:-len(ext)]
    return str(bid or "")


def _video_kind(data):
    # "mp4" for an ISO base media file, which covers both .mp4 and .mov, "webm" for a WebM file, else None.
    # Read from the first bytes, because an upload arrives without a file name. A MOV is the same container
    # as an MP4 with the brand 'qt  ', so one check serves both. WebM is accepted too, so a character archive
    # that already holds a converted background can be imported.
    if len(data) >= 12 and data[4:8] in (b"ftyp", b"moov"):
        return "mp4"
    if len(data) >= 4 and data[:4] == b"\x1a\x45\xdf\xa3":
        return "webm"
    return None


def video_tools():
    # The two ffmpeg programs this needs, or None when they are not installed. Video is the only feature
    # that needs a program outside the Python wheel set, so it is checked before anything is transcoded.
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        return None
    return ffmpeg, ffprobe


def _require_tools():
    tools = video_tools()
    if tools is None:
        raise EditorError("Video backgrounds need ffmpeg on this server. Install ffmpeg, or upload a picture.")
    return tools


def _probe(ffprobe, path):
    # Width, height, duration and rotation of the first video stream. Raises EditorError when the file has
    # no readable video stream.
    proc = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0",
                           "-show_entries", "stream=width,height,duration:stream_tags=rotate"
                           ":stream_side_data=rotation:format=duration",
                           "-of", "json", path], capture_output=True, timeout=30)
    if proc.returncode != 0:
        raise EditorError("Could not read that video. Use an MP4 or MOV file.")
    try:
        info = json.loads(proc.stdout.decode("utf-8", "replace") or "{}")
    except ValueError:
        raise EditorError("Could not read that video. Use an MP4 or MOV file.") from None
    streams = info.get("streams") or []
    if not streams:
        raise EditorError("That file has no video in it.")
    stream = streams[0]
    width = int(stream.get("width") or 0)
    height = int(stream.get("height") or 0)
    if not width or not height:
        raise EditorError("Could not read that video's picture size.")
    rotation = 0
    for item in stream.get("side_data_list") or []:
        if "rotation" in item:
            rotation = int(float(item["rotation"]))
    if not rotation and stream.get("tags", {}).get("rotate"):
        try:
            rotation = int(float(stream["tags"]["rotate"]))
        except (TypeError, ValueError):
            rotation = 0
    # A phone records landscape and marks the picture rotated. Judge the size the viewer sees, or a
    # portrait clip would fail the 720p test on its width.
    if abs(rotation) % 180 == 90:
        width, height = height, width
    duration = None
    for source in (stream.get("duration"), (info.get("format") or {}).get("duration")):
        try:
            duration = float(source)
            break
        except (TypeError, ValueError):
            continue
    return {"width": width, "height": height, "duration": duration}


def transcode_video(data):
    # Checks an MP4 or MOV upload and returns (webm bytes, mp4 bytes, poster jpeg bytes). The video is re-encoded
    # twice: VP9 in WebM, which is small, and H.264 in MP4, which every phone and browser can play. Its audio
    # track is dropped (a background has no sound), and the first frame becomes a poster so a paused page has a
    # still to show. The blur and dimming are applied once, to the WebM and the poster; the MP4 is made from the
    # finished WebM, so the look is never applied twice. Looping is the player's job.
    if not data:
        raise EditorError("Choose a video first.")
    if len(data) > VIDEO_MAX_BYTES:
        raise EditorError(f"A background video can be up to {VIDEO_MAX_BYTES // (1024 * 1024)} MB.")
    if _video_kind(data) is None:
        raise EditorError("A background video must be an MP4 or MOV file.")
    ffmpeg, ffprobe = _require_tools()
    work = tempfile.mkdtemp(prefix="miso-video-")
    try:
        source = os.path.join(work, "in" + (".webm" if _video_kind(data) == "webm" else ".mp4"))
        with open(source, "wb") as f:
            f.write(data)
        meta = _probe(ffprobe, source)
        width, height = meta["width"], meta["height"]
        if min(width, height) < VIDEO_MIN_SIDE:
            raise EditorError(f"A background video must be at least {VIDEO_MIN_SIDE}p on its shorter side; "
                              f"this one is {width}x{height}.")
        if meta["duration"] is not None and meta["duration"] > VIDEO_MAX_SECONDS:
            raise EditorError(f"A background video can be up to {VIDEO_MAX_SECONDS} seconds; "
                              f"this one is {meta['duration']:.0f} seconds.")
        # Never enlarge: the box shrinks to the source when the source is already small enough.
        scale = ("scale='min(1280,iw)':'min(1280,ih)':force_original_aspect_ratio=decrease"
                 ":force_divisible_by=2")
        out = os.path.join(work, "out.webm")
        proc = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", source,
                               "-an", "-sn", "-dn",           # no audio, subtitles or data tracks
                               "-c:v", "libvpx-vp9", "-crf", str(VIDEO_CRF), "-b:v", "0",
                               "-row-mt", "1", "-cpu-used", "4", "-deadline", "good",
                               "-vf", scale + "," + VIDEO_LOOK + ",format=yuv420p",
                               "-t", str(VIDEO_MAX_SECONDS), out],
                              capture_output=True, timeout=600)
        if proc.returncode != 0 or not os.path.isfile(out) or os.path.getsize(out) == 0:
            detail = (proc.stderr or b"").decode("utf-8", "replace").strip().splitlines()
            raise EditorError("That video could not be converted." + ((" " + detail[-1]) if detail else ""))
        poster = os.path.join(work, "poster.jpg")
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", "0", "-i", source,
                        "-frames:v", "1", "-vf", scale + "," + VIDEO_LOOK, "-q:v", "4", poster],
                       capture_output=True, timeout=120)
        if not os.path.isfile(poster) or os.path.getsize(poster) == 0:
            raise EditorError("That video could not be converted: no first frame to show.")
        with open(out, "rb") as f:
            webm = f.read()
        # The MP4 copy: H.264 from the finished WebM. Old iPhones cannot play VP9, so without this the page would
        # only ever show the poster frame on those devices. It is written with faststart so it begins playing
        # before the whole file has arrived.
        fallback = os.path.join(work, "out.mp4")
        proc = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", out,
                               "-an", "-c:v", "libx264", "-crf", str(VIDEO_FALLBACK_CRF),
                               "-preset", "veryfast", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                               fallback],
                              capture_output=True, timeout=600)
        if proc.returncode != 0 or not os.path.isfile(fallback) or os.path.getsize(fallback) == 0:
            raise EditorError("That video could not be prepared for phones.")
        with open(fallback, "rb") as f:
            mp4 = f.read()
        with open(poster, "rb") as f:
            still = f.read()
        return webm, mp4, still
    except subprocess.TimeoutExpired:
        raise EditorError("That video took too long to convert. Try a shorter clip.") from None
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _encode_background(data):
    # Checks an upload and returns (bytes, extension). A picture is downsized to PNG with the page's colour look
    # already applied; an animated GIF is kept exactly as uploaded so it keeps moving.
    if not data:
        raise EditorError("Choose an image first.")
    if len(data) > UPLOAD_MAX:
        raise EditorError("Background uploads can be up to 10 MB.")
    try:
        img = Image.open(io.BytesIO(data))
        if img.format not in ("PNG", "JPEG", "WEBP", "GIF"):
            raise EditorError("Background must be a PNG, JPEG, WebP or GIF image.")
        fmt = img.format   # exif_transpose returns a copy, and the copy loses this
        if img.width * img.height > MAX_SOURCE_PIXELS:
            raise EditorError("That image is too large. Use one under 40 megapixels.")
        img = ImageOps.exif_transpose(img)
        img.load()
    except EditorError:
        raise
    except (OSError, ValueError, Image.DecompressionBombError) as err:
        raise EditorError("Could not read that image. Use a PNG, JPEG, WebP or GIF.") from err
    if fmt == "GIF":
        animated = _animated_gif(data)
        if animated is not None:
            return animated, ".gif"   # stored as uploaded: the colour look would flatten the loop
    img = img.convert("RGB")
    img.thumbnail((BG_MAX_SIDE, BG_MAX_SIDE), Image.LANCZOS)   # never enlarges
    img = img.convert("RGB", PIL_LOOK)   # the colour look, baked in once so the page does not filter it live
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue(), BG_IMAGE_EXT


def background_bytes(data):
    # Checked upload, downsized as PNG or kept as an animated GIF. Returns (bytes, extension).
    # Also used for account profile backgrounds.
    return _encode_background(data)


def background_ids(root, name):
    # The legacy single file first, then the rest in name order. A video id keeps its extension. Empty when
    # there are none.
    try:
        folder = folder_of(root, name)
    except EditorError:
        return []
    ids = []
    if os.path.isfile(os.path.join(folder, BG_LEGACY)):
        ids.append(LEGACY_ID)
    bdir = os.path.join(folder, BG_DIR)
    if os.path.isdir(bdir):
        for f in sorted(os.listdir(bdir)):
            stem, ext = os.path.splitext(f)
            if not BG_ID_RE.match(stem) or not os.path.isfile(os.path.join(bdir, f)):
                continue
            if ext in BG_IMAGE_EXTS:
                ids.append(stem)
            elif ext == VIDEO_STORE_EXT:
                ids.append(stem + ext)
    return ids


_GIF_LOOP_CACHE = {}


def _gif_loop_ms(path):
    # One loop of an animated GIF in milliseconds, cached by the file's stamp. A delay under 20 ms is
    # counted as 100 ms, which is what Chrome plays.
    try:
        stamp = os.stat(path)
    except OSError:
        return 0
    key = (path, stamp.st_mtime_ns, stamp.st_size)
    if key in _GIF_LOOP_CACHE:
        return _GIF_LOOP_CACHE[key]
    total = 0
    try:
        with Image.open(path) as im:
            for i in range(getattr(im, "n_frames", 1)):
                im.seek(i)
                delay = int(im.info.get("duration") or 0)
                total += 100 if delay < 20 else delay
    except Exception:
        total = 0
    if len(_GIF_LOOP_CACHE) > 512:
        _GIF_LOOP_CACHE.clear()
    _GIF_LOOP_CACHE[key] = total
    return total


def background_meta(root, name):
    # What each background is, for the gallery: a video, an animated GIF with the length of one loop, or a
    # still. The page waits for moving art to finish before it fades to the next piece.
    try:
        folder = folder_of(root, name)
    except EditorError:
        return {}
    out = {}
    if os.path.isfile(os.path.join(folder, BG_LEGACY)):
        out[LEGACY_ID] = {"kind": "still"}
    bdir = os.path.join(folder, BG_DIR)
    if not os.path.isdir(bdir):
        return out
    for f in sorted(os.listdir(bdir)):
        stem, ext = os.path.splitext(f)
        path = os.path.join(bdir, f)
        if not BG_ID_RE.match(stem) or not os.path.isfile(path):
            continue
        if ext == VIDEO_STORE_EXT:
            out[stem + ext] = {"kind": "video"}
        elif ext == ".gif":
            out[stem] = {"kind": "gif", "duration_ms": _gif_loop_ms(path)}
        elif ext in BG_IMAGE_EXTS:
            out[stem] = {"kind": "still"}
    return out


def poster_path(root, name, bid):
    # The still frame stored beside a video background, used as its poster and as its reduced-motion
    # picture. None when the id is not a video or the frame is missing.
    if not is_video_id(bid) or not BG_ANY_ID_RE.match(bid or ""):
        return None
    try:
        folder = folder_of(root, name)
    except EditorError:
        return None
    p = os.path.join(folder, BG_DIR, _video_stem(bid) + VIDEO_POSTER_SUFFIX)
    return p if os.path.isfile(p) else None


# One conversion at a time: two requests for the same new MP4 must not both run ffmpeg over it.
_FALLBACK_LOCK = threading.Lock()


def ensure_video_fallback(source, target):
    # Writes an H.264 MP4 from an already converted WebM, for browsers that cannot play VP9. The blur and the
    # colour look are already in the source, so they are not applied again. True when the file is there.
    if os.path.isfile(target) and os.path.getsize(target) > 0:
        return True
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not os.path.isfile(source):
        return False
    with _FALLBACK_LOCK:
        if os.path.isfile(target) and os.path.getsize(target) > 0:
            return True   # another request finished it while this one waited
        tmp = target + ".part.mp4"   # ffmpeg picks the writer from the extension
        try:
            proc = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", source,
                                   "-an", "-c:v", "libx264", "-crf", str(VIDEO_FALLBACK_CRF),
                                   "-preset", "veryfast", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                                   "-f", "mp4", tmp],
                                  capture_output=True, timeout=600)
            if proc.returncode != 0 or not os.path.isfile(tmp) or os.path.getsize(tmp) == 0:
                if os.path.isfile(tmp):
                    os.remove(tmp)
                return False
            os.replace(tmp, target)
            return True
        except (OSError, subprocess.SubprocessError):
            if os.path.isfile(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            return False


def _bg_file(root, name, bid):
    # The file for one id, or None. Ids are checked before they touch the disk. A poster id is accepted too,
    # so the page can ask for the still frame with the same route. An MP4 that a background from before this
    # store gained is made from its WebM on the first request.
    if bid != LEGACY_ID and not (BG_ANY_ID_RE.match(bid or "") or POSTER_ID_RE.match(bid or "")):
        return None
    try:
        folder = folder_of(root, name)
    except EditorError:
        return None
    if bid == LEGACY_ID:
        p = os.path.join(folder, BG_LEGACY)
    elif POSTER_ID_RE.match(bid):
        p = os.path.join(folder, BG_DIR, bid)
    elif is_video_id(bid):
        p = os.path.join(folder, BG_DIR, bid)
        if not os.path.isfile(p) and bid.lower().endswith(VIDEO_FALLBACK_EXT):
            source = os.path.join(folder, BG_DIR, _video_stem(bid) + VIDEO_STORE_EXT)
            p = p if ensure_video_fallback(source, p) else None
    else:
        p = None
        for ext in BG_IMAGE_EXTS:
            if os.path.isfile(os.path.join(folder, BG_DIR, bid + ext)):
                p = os.path.join(folder, BG_DIR, bid + ext)
                break
    return p if p and os.path.isfile(p) else None


def background_path(root, name, bid=None):
    # With no id, the first background. Cards and chats ask for a specific one.
    if bid is None:
        ids = background_ids(root, name)
        if not ids:
            return None
        bid = ids[0]
    return _bg_file(root, name, bid)


def add_background(root, name, data, poster=None, stored=False):
    # Adds one background and returns its id. Refuses past MAX_BACKGROUNDS. A picture becomes a PNG, or stays an
    # animated GIF. An MP4 or MOV is transcoded to WebM and MP4 with its audio dropped, and its first frame is
    # stored beside it as a poster. stored=True means data is already a background this app wrote (an archive made
    # here), so it is saved as it is rather than encoded again: the colour look and the blur are never applied twice.
    path = folder_of(root, name)
    if not os.path.isdir(path):
        raise EditorError("No such character.")
    fallback = None
    if stored:
        if len(data) > VIDEO_MAX_BYTES:
            raise EditorError(f"A background video can be up to {VIDEO_MAX_BYTES // (1024 * 1024)} MB.")
        if _video_kind(data) == "webm":
            payload, still, ext = data, poster, VIDEO_STORE_EXT
        elif data[:8] == b"\x89PNG\r\n\x1a\n":
            payload, still, ext = data, None, ".png"
        elif data[:3] == b"\xff\xd8\xff":
            payload, still, ext = data, None, ".jpg"
        elif data[:6] in (b"GIF87a", b"GIF89a"):
            payload, still, ext = data, None, ".gif"
        else:
            raise EditorError("A stored background must be a WebM, PNG, JPEG or GIF file.")
    elif _video_kind(data) is not None:
        payload, fallback, still = transcode_video(data)
        ext = VIDEO_STORE_EXT
    else:
        payload, ext = _encode_background(data)
        still = None
    if len(background_ids(root, name)) >= MAX_BACKGROUNDS:
        raise EditorError(f"A character can have up to {MAX_BACKGROUNDS} backgrounds. Remove one first.")
    bdir = os.path.join(path, BG_DIR)
    os.makedirs(bdir, exist_ok=True)
    bid = secrets.token_hex(6)
    while any(os.path.exists(os.path.join(bdir, bid + e))
              for e in (*BG_IMAGE_EXTS, VIDEO_STORE_EXT, VIDEO_FALLBACK_EXT)):
        bid = secrets.token_hex(6)
    tmp = os.path.join(bdir, bid + ".tmp")
    with open(tmp, "wb") as f:
        f.write(payload)
    os.replace(tmp, os.path.join(bdir, bid + ext))
    if fallback:
        tmp = os.path.join(bdir, bid + VIDEO_FALLBACK_EXT + ".tmp")
        with open(tmp, "wb") as f:
            f.write(fallback)
        os.replace(tmp, os.path.join(bdir, bid + VIDEO_FALLBACK_EXT))
    if still is not None:
        ptmp = os.path.join(bdir, bid + ".ptmp")
        with open(ptmp, "wb") as f:
            f.write(still)
        os.replace(ptmp, os.path.join(bdir, bid + VIDEO_POSTER_SUFFIX))
    return bid if ext in BG_IMAGE_EXTS else bid + ext


def remove_background(root, name, bid):
    p = _bg_file(root, name, bid)
    if p is None:
        raise EditorError("No such background.")
    poster = poster_path(root, name, bid)
    os.remove(p)
    if is_video_id(bid):
        # The MP4 copy and the poster belong to the video and go with it.
        for extra in (os.path.join(os.path.dirname(p), _video_stem(bid) + VIDEO_FALLBACK_EXT),
                      os.path.join(os.path.dirname(p), _video_stem(bid) + VIDEO_STORE_EXT)):
            if os.path.isfile(extra):
                os.remove(extra)
    if poster:
        os.remove(poster)


def _shrink_icon(data):
    # Crop to a centred square, then keep the highest resolution that stays under ICON_OUT_MAX.
    # Returns (bytes, extension): always WebP, with transparency kept when the source has it. Never upscales.
    # A still GIF is read like a PNG; an animated one never reaches here, because portrait_bytes keeps it whole.
    try:
        img = Image.open(io.BytesIO(data))
        if img.format not in ("PNG", "JPEG", "WEBP", "GIF"):
            raise EditorError("Picture must be a PNG, JPEG, WebP or GIF image.")
        if img.width * img.height > MAX_SOURCE_PIXELS:
            raise EditorError("That image is too large. Use one under 40 megapixels.")
        img = ImageOps.exif_transpose(img)
        img.load()
    except EditorError:
        raise
    except (OSError, ValueError, Image.DecompressionBombError) as err:
        raise EditorError("Could not read that image. Use a PNG, JPEG, WebP or GIF.") from err
    alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
    img = img.convert("RGBA" if alpha else "RGB")
    side = min(img.size)
    left, top = (img.width - side) // 2, (img.height - side) // 2
    img = img.crop((left, top, left + side, top + side))
    for size in sorted({min(s, side) for s in ICON_SIDES}, reverse=True):
        small = img.resize((size, size), Image.LANCZOS)
        for quality in (90, 80, 70):
            buf = io.BytesIO()
            small.save(buf, "WEBP", quality=quality, method=6)
            if len(buf.getvalue()) < ICON_OUT_MAX:
                return buf.getvalue(), "webp"
    raise EditorError("That picture is too detailed to fit under 512 KB. Try a simpler image.")


def _animated_gif(data):
    # Animated GIFs are kept exactly as uploaded: re-encoding to WebP would flatten the loop to one frame.
    # Returns the original bytes, or None when the upload is not an animated GIF. Raises EditorError when it
    # is one but cannot be stored as it is.
    try:
        img = Image.open(io.BytesIO(data))
    except (OSError, ValueError, Image.DecompressionBombError):
        return None
    if img.format != "GIF" or getattr(img, "n_frames", 1) <= 1:
        return None
    if len(data) > GIF_MAX_BYTES:
        raise EditorError(f"An animated GIF can be up to {GIF_MAX_BYTES // (1024 * 1024)} MB.")
    if img.width * img.height > MAX_SOURCE_PIXELS:
        raise EditorError("That image is too large. Use one under 40 megapixels.")
    if img.n_frames > GIF_MAX_FRAMES:
        raise EditorError(f"An animated GIF can have up to {GIF_MAX_FRAMES} frames. Try a shorter loop.")
    return data


def portrait_bytes(data):
    # Checked upload, cropped to a centred square and stored as WebP. An animated GIF is the exception: it is
    # returned untouched so it keeps moving. Returns (bytes, extension). Account profile pictures use this too.
    if not data:
        raise EditorError("Choose an image first.")
    if len(data) > UPLOAD_MAX:
        raise EditorError("Picture uploads can be up to 10 MB.")
    animated = _animated_gif(data)
    if animated is not None:
        return animated, "gif"
    return _shrink_icon(data)


def save_icon(root, name, data):
    # Store a character's icon, replacing any old one. Accepts a PNG, JPEG, WebP or GIF up to 10 MB; a still
    # picture is cropped to a centred square and its resolution lowered only as far as needed to stay under
    # 512 KB, while an animated GIF is kept as uploaded. Returns the file name.
    path = folder_of(root, name)
    if not os.path.isdir(path):
        raise EditorError("No such character.")
    if not data:
        raise EditorError("Choose an image first.")
    if len(data) > UPLOAD_MAX:
        raise EditorError("Icon uploads can be up to 10 MB.")
    out, ext = portrait_bytes(data)
    for old in ICON_FILES:
        if os.path.exists(os.path.join(path, old)):
            os.remove(os.path.join(path, old))
    target = "icon." + ext
    with open(os.path.join(path, target), "wb") as f:
        f.write(out)
    return target


def icon_path(root, name):
    path = folder_of(root, name)
    for fname in ICON_FILES:
        p = os.path.join(path, fname)
        if os.path.isfile(p):
            return p
    return None


def example_card():
    return EXAMPLE_CARD


def example_messages():
    return [dict(pair) for pair in EXAMPLE_MESSAGES]
