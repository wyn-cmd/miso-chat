"""Speech for character replies.

Reply text is posted to a speech server (a llama.cpp build with an audio model, or any OpenAI-compatible speech
endpoint) which returns WAV audio. A character's author may store a voice and a short reference clip; an account
may keep its own profiles, each with its own reference clip.
"""
import base64
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request
import wave

from charname import folder_for
from web import editor

VOICE_DIR = "voice"
CHAR_FILE = "voice.json"
USER_FILE = "voice.json"
SAMPLE_NAME = "sample.wav"
SAMPLES_DIR = "samples"
CACHE_DIR = "voice-cache"
PROFILE_ID_RE = re.compile(r"^[0-9a-f]{12}$")
SAMPLE_MAX = 20 * 1024 * 1024
SAMPLE_SECONDS = 20
TEXT_MAX = 4000
CACHE_MAX = 200 * 1024 * 1024
CACHE_PRUNE = 150 * 1024 * 1024
SPEECH_MAX = 50 * 1024 * 1024          # one spoken line should never come back larger than this
CHUNK_CHARS = 1200                     # a long reply is read in parts; the model fails on a few thousand chars at once
STT_MAX = 25 * 1024 * 1024             # one recording from a voice call
STT_SECONDS = 120                      # a call utterance is short; a longer upload is cut at this point
TRANSCRIPT_MAX = 1024 * 1024           # a transcript is text, so it is never allowed to grow like audio

# The speech languages LFM3-Audio understands. A code outside this list is refused rather than guessed at.
LANGS = {
    "en": "English", "en-GB": "British English", "en-US": "American English",
    "ar": "Arabic", "da": "Danish", "de": "German", "el": "Greek", "fi": "Finnish",
    "fr": "French", "he": "Hebrew", "it": "Italian", "ja": "Japanese", "ko": "Korean",
    "nl": "Dutch", "pl": "Polish", "pt": "Portuguese", "es": "Spanish", "sv": "Swedish",
    "tr": "Turkish", "zh": "Chinese",
}
VOICES = ("female", "male")

USER_DEFAULTS = {
    "auto": False,
    "rate": 1.0,
    "pitch": 1.0,
    "volume": 1.0,
    "dialogue_only": False,
    "choice": "character",
    "charChoices": {},
    "profiles": {},
}
CHAR_DEFAULTS = {"voice": "", "lang": "", "sample": ""}


class VoiceError(ValueError):
    pass


def _clamp(value, lo, hi, fallback):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return fallback
    if n != n:  # NaN
        return fallback
    return max(lo, min(hi, n))


def _read_json(path, fallback):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return dict(fallback)
    return data if isinstance(data, dict) else dict(fallback)


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def _write_bytes(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def _tools():
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise VoiceError("Voice calls and samples need ffmpeg on this server. Install ffmpeg, then try again.")
    return ffmpeg, ffprobe


def _probe_duration(ffprobe, path):
    proc = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration",
                           "-of", "default=nw=1:nk=1", path], capture_output=True, timeout=60)
    if proc.returncode != 0:
        raise VoiceError("Could not read that audio file. Use a WAV, MP3, M4A, Ogg, Opus, WebM or FLAC file.")
    try:
        return float((proc.stdout or b"").decode("utf-8", "replace").strip())
    except ValueError:
        raise VoiceError("Could not read how long that audio file is.") from None


def _clean_filter(chain, source, out, rate=24000):
    ffmpeg, _ = _tools()
    proc = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", source,
                           "-vn", "-sn", "-dn", "-ac", "1", "-ar", str(rate), "-c:a", "pcm_s16le",
                           "-af", chain, out], capture_output=True, timeout=300)
    return proc.returncode == 0


def convert_sample(data):
    # A reference clip is cut to one voice, trimmed of silence, evened out and stored as 24 kHz mono PCM WAV.
    if not data:
        raise VoiceError("Choose an audio file first.")
    try:
        editor._scan_bytes(data)
    except editor.EditorError as err:
        raise VoiceError(str(err)) from None
    if len(data) > SAMPLE_MAX:
        raise VoiceError(f"A voice sample can be up to {SAMPLE_MAX // (1024 * 1024)} MB.")
    ffmpeg, ffprobe = _tools()
    work = tempfile.mkdtemp(prefix="miso-voice-")
    try:
        source = os.path.join(work, "source")
        with open(source, "wb") as f:
            f.write(data)
        duration = _probe_duration(ffprobe, source)
        if not duration or duration <= 0:
            raise VoiceError("That file has no sound in it.")
        if duration < 0.8:
            raise VoiceError("That clip is too short. Use at least a second of clear speech.")
        out = os.path.join(work, "sample.wav")
        chain = ("silenceremove=start_periods=1:start_silence=0.1:start_threshold=-45dB,"
                 "areverse,silenceremove=start_periods=1:start_silence=0.1:start_threshold=-45dB,"
                 "areverse,loudnorm=I=-18:TP=-2:LRA=11,"
                 f"atrim=0:{SAMPLE_SECONDS},aresample=24000")
        if not _clean_filter(chain, source, out):
            # A very quiet or very short clip can defeat loudnorm; keep the clip and skip the levelling.
            fallback = f"atrim=0:{SAMPLE_SECONDS},aresample=24000"
            if not _clean_filter(fallback, source, out):
                raise VoiceError("That clip could not be converted. Try a WAV or MP3 file.")
        with open(out, "rb") as f:
            return f.read()
    except subprocess.TimeoutExpired:
        raise VoiceError("That clip took too long to convert. Try a shorter one.") from None
    finally:
        shutil.rmtree(work, ignore_errors=True)


def clean_text(text, dialogue_only=False, strict_dialogue=False):
    # Markdown and stage directions are turned into plain sentences before the speech server sees them.
    s = str(text or "")
    s = re.sub(r"```.*?```", " ", s, flags=re.S)
    s = re.sub(r"`[^`]*`", " ", s)
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", s)
    s = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"^\s{0,3}#{1,6}\s*", "", s, flags=re.M)
    s = re.sub(r"^\s{0,3}[-*+]\s+", "", s, flags=re.M)
    s = re.sub(r"^\s{0,3}>\s?", "", s, flags=re.M)
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s, flags=re.S)
    s = re.sub(r"__(.+?)__", r"\1", s, flags=re.S)
    s = re.sub(r"\*(.+?)\*", r"\1", s, flags=re.S)
    s = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"\1", s, flags=re.S)
    s = s.replace("*", " ").replace("`", " ").replace("~", " ").replace("|", " ")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\s*\n\s*", " ", s).strip()
    if dialogue_only:
        quoted = re.findall(r'"([^"]+)"|\u201c([^\u201d]+)\u201d|\u300c([^\u300d]+)\u300d|\u300e([^\u300f]+)\u300f', s)
        lines = [next(x for x in group if x) for group in quoted if any(group)]
        if lines:
            s = " ".join(lines)
        elif strict_dialogue:
            # In a call only spoken lines are used. Narration alone leaves nothing to say.
            return ""
    return s[:TEXT_MAX].strip()


def _base(url):
    return str(url or "").strip().rstrip("/")


def _llama_url(url):
    base = _base(url)
    if base.endswith("/v1"):
        base = base[:-3].rstrip("/")
    return base + "/tts"


def _openai_url(url):
    base = _base(url)
    if not base.endswith("/v1"):
        base += "/v1"
    return base + "/audio/speech"


def _transcribe_url(url):
    # Both styles answer the OpenAI transcription route. A llama.cpp server started with an audio model
    # exposes it at /v1/audio/transcriptions, beside /tts.
    base = _base(url)
    if base.endswith("/audio/transcriptions"):
        return base
    if base.endswith("/v1"):
        return base + "/audio/transcriptions"
    return base + "/v1/audio/transcriptions"


def list_models(base_url, style="llama", api_key="", timeout=10):
    # The ids the speech server says it can run. llama.cpp and OpenAI-style servers both answer GET /v1/models.
    base = _base(base_url)
    if not base:
        raise VoiceError("The speech server has no address.")
    if style == "openai" and not base.endswith("/v1"):
        base += "/v1"
    url = base + "/models" if base.endswith("/v1") else base + "/v1/models"
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read(1000000).decode("utf-8", "replace"))
    except urllib.error.HTTPError as err:
        raise VoiceError(f"The speech server refused the model list ({err.code}).") from None
    except urllib.error.URLError as err:
        raise VoiceError(f"The speech server is not reachable: {err.reason}") from None
    except OSError as err:
        raise VoiceError(f"The speech server is not reachable: {err}") from None
    except ValueError:
        raise VoiceError("The speech server did not return a model list.") from None
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):
        items = data.get("models") if isinstance(data, dict) else None
    out = []
    for item in items or []:
        mid = item.get("id") if isinstance(item, dict) else item
        if isinstance(mid, str) and mid and mid not in out:
            out.append(mid)
    return sorted(out)


def _post_json(url, payload, api_key, timeout):
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "audio/wav, application/octet-stream"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read(SPEECH_MAX + 1)
            if len(data) > SPEECH_MAX:
                raise VoiceError("The speech server returned too much audio in one reply.")
            return data
    except urllib.error.HTTPError as err:
        detail = ""
        try:
            detail = err.read().decode("utf-8", "replace")[:400]
        except OSError:
            pass
        if detail.strip().startswith("{"):
            try:
                detail = str(json.loads(detail).get("error", detail))
            except ValueError:
                pass
        raise VoiceError(f"The speech server refused the request ({err.code}). {detail}".strip()) from None
    except urllib.error.URLError as err:
        raise VoiceError(f"The speech server is not reachable: {err.reason}") from None
    except OSError as err:
        raise VoiceError(f"The speech server is not reachable: {err}") from None


def _wav(data):
    if not data or len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise VoiceError("The speech server returned something that is not a WAV file.")
    return data


# ---- speech to text: a call records the reader, and the same speech server turns it into words

def convert_call_audio(data):
    # Browser recordings arrive as WebM/Opus, Ogg or MP4. Speech models take 16 kHz mono PCM WAV.
    if not data:
        raise VoiceError("There was no audio to send.")
    if len(data) > STT_MAX:
        raise VoiceError(f"A recording can be up to {STT_MAX // (1024 * 1024)} MB.")
    try:
        editor._scan_bytes(data)
    except editor.EditorError as err:
        raise VoiceError(str(err)) from None
    _tools()   # fail early with a clear message when ffmpeg or ffprobe is missing
    work = tempfile.mkdtemp(prefix="miso-call-")
    try:
        source = os.path.join(work, "source")
        with open(source, "wb") as f:
            f.write(data)
        # Browser recordings often carry no duration in the header, so the length check is taken from the converted
        # WAV. Probing first would spend a second process and a round trip for a value ffmpeg gives us anyway.
        out = os.path.join(work, "call.wav")
        if not _clean_filter(f"atrim=0:{STT_SECONDS},aresample=16000", source, out, 16000):
            raise VoiceError("That recording could not be converted.")
        with open(out, "rb") as f:
            wav = f.read()
        if len(wav) < 12 + 16000 * 2 // 4:   # header plus about a quarter second of 16 kHz mono PCM
            raise VoiceError("That recording is too short.")
        return wav
    except subprocess.TimeoutExpired:
        raise VoiceError("That recording took too long to convert. Try a shorter one.") from None
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _multipart(fields, file_field, filename, content_type, data):
    boundary = "----miso" + secrets.token_hex(12)
    body = io.BytesIO()
    for name, value in fields.items():
        body.write(("--" + boundary + "\r\n").encode("ascii"))
        body.write(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("ascii"))
        body.write(str(value).encode("utf-8"))
        body.write(b"\r\n")
    body.write(("--" + boundary + "\r\n").encode("ascii"))
    body.write(f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
               .encode("ascii"))
    body.write(("Content-Type: " + content_type + "\r\n\r\n").encode("ascii"))
    body.write(data)
    body.write(b"\r\n")
    body.write(("--" + boundary + "--\r\n").encode("ascii"))
    return body.getvalue(), "multipart/form-data; boundary=" + boundary


def _post_multipart(url, body, content_type, api_key, timeout):
    headers = {"Content-Type": content_type, "Accept": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read(TRANSCRIPT_MAX + 1)
            if len(data) > TRANSCRIPT_MAX:
                raise VoiceError("The speech server returned too much text.")
            return data
    except urllib.error.HTTPError as err:
        detail = ""
        try:
            detail = err.read().decode("utf-8", "replace")[:400]
        except OSError:
            pass
        if detail.strip().startswith("{"):
            try:
                detail = str(json.loads(detail).get("error", detail))
            except ValueError:
                pass
        raise VoiceError(f"The speech server refused the recording ({err.code}). {detail}".strip()) from None
    except urllib.error.URLError as err:
        raise VoiceError(f"The speech server is not reachable: {err.reason}") from None
    except OSError as err:
        raise VoiceError(f"The speech server is not reachable: {err}") from None


_ANALYSIS_MARK = re.compile(r"<\|channel\|>", re.I)
_FINAL_CHANNEL = re.compile(r"<\|channel\|>\s*final\s*<\|message\|>(.*?)(?=<\|channel\|>|$)", re.I | re.S)
_MESSAGE_MARK = re.compile(r"<\|message\|>", re.I)


def _clean_transcript(text):
    # An audio model may prepend a short description of the clip before the words, or answer in named
    # channels. Only the words are kept.
    s = str(text or "").strip()
    final = _FINAL_CHANNEL.search(s)
    if final:
        s = final.group(1)
    else:
        message = _MESSAGE_MARK.search(s)
        if message:
            s = s[message.end():]
        elif _ANALYSIS_MARK.search(s):
            s = _ANALYSIS_MARK.split(s)[-1]
    s = re.sub(r"<\|[^>]*>", " ", s)
    s = re.sub(r"^\s*(analysis|final)\s*", "", s, flags=re.I)
    s = re.sub(r"^\s*(the\s+)?(user|speaker|audio|clip|transcript|transcription)\s+(says|said|is saying)"
               r"\s*[:\uff1a]\s*", "", s, flags=re.I)
    s = re.sub(r"^\s*\[[^\]]{0,60}\]\s*", "", s)
    s = s.strip().strip('"').strip()
    s = re.sub(r"\s+", " ", s)
    if s.lower() in ("", "[blank_audio]", "[silence]", "(silence)", "silence", "..."):
        return ""
    return s[:TEXT_MAX]


def _read_transcript(data):
    raw = data.decode("utf-8", "replace") if isinstance(data, bytes) else str(data or "")
    try:
        obj = json.loads(raw)
    except ValueError:
        return _clean_transcript(raw)
    text = ""
    if isinstance(obj, dict):
        text = obj.get("text") or obj.get("transcription") or obj.get("transcript") or obj.get("delta") or ""
        segments = obj.get("segments")
        if not text and isinstance(segments, list):
            text = " ".join(str(seg.get("text") or "") for seg in segments if isinstance(seg, dict))
    return _clean_transcript(text)


def transcribe(cfg, data, lang=""):
    # One recording to the speech server, which answers with the words it heard.
    if not cfg.get("enabled"):
        raise VoiceError("Speech is turned off on this server.")
    base_url = cfg.get("base_url") or ""
    if not base_url:
        raise VoiceError("The speech server has no address.")
    lang = lang if lang in LANGS else (cfg.get("lang") or "en")
    wav = convert_call_audio(data)
    fields = {"response_format": "json", "language": lang.split("-")[0]}
    if cfg.get("style") == "openai":
        fields["model"] = cfg.get("model") or "whisper-1"
    body, content_type = _multipart(fields, "file", "call.wav", "audio/wav", wav)
    raw = _post_multipart(_transcribe_url(base_url), body, content_type, cfg.get("api_key") or "",
                          int(cfg.get("timeout") or 60))
    return _read_transcript(raw)


def synthesize(cfg, text, voice="", lang="", sample_path="", rate=1.0, pitch=1.0):
    # One request to the configured speech server. Reference audio is sent as base64 in the llama.cpp form.
    text = str(text or "").strip()
    if not text:
        raise VoiceError("There is nothing to read.")
    if not cfg.get("enabled"):
        raise VoiceError("Speech is turned off on this server.")
    base_url = cfg.get("base_url") or ""
    if not base_url:
        raise VoiceError("The speech server has no address.")
    voice = voice if voice in VOICES else (cfg.get("voice") or "female")
    lang = lang if lang in LANGS else (cfg.get("lang") or "en")
    timeout = int(cfg.get("timeout") or 60)
    if cfg.get("style") == "openai":
        # Speed is applied here rather than by the server, so both styles change the sound the same way.
        payload = {"model": cfg.get("model") or "tts-1", "input": text, "voice": voice,
                   "response_format": "wav", "speed": 1.0}
        data = _post_json(_openai_url(base_url), payload, cfg.get("api_key") or "", timeout)
    else:
        payload = {"input": text, "lang": lang, "voice": voice}
        if sample_path and os.path.isfile(sample_path):
            with open(sample_path, "rb") as f:
                payload["speaker_ref_b64"] = base64.b64encode(f.read()).decode("ascii")
        data = _post_json(_llama_url(base_url), payload, cfg.get("api_key") or "", timeout)
    data = _wav(data)
    return postprocess(data, rate, pitch, int(cfg.get("sample_rate") or 24000))


def _atempo(factor):
    # atempo only accepts 0.5 to 2.0 in one pass, so a larger change is split across passes.
    steps = []
    f = factor
    while f > 2.0:
        steps.append(2.0)
        f /= 2.0
    while f < 0.5:
        steps.append(0.5)
        f /= 0.5
    steps.append(f)
    return ",".join("atempo=%.4f" % s for s in steps)


def postprocess(data, rate=1.0, pitch=1.0, sample_rate=24000):
    # Speed and pitch are changed only when asked for, so the common case is the server's own audio untouched.
    rate = _clamp(rate, 0.5, 2.0, 1.0)
    pitch = _clamp(pitch, 0.5, 2.0, 1.0)
    if abs(rate - 1.0) < 0.01 and abs(pitch - 1.0) < 0.01:
        return data
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return data
    filters = []
    if abs(pitch - 1.0) >= 0.01:
        # The rate is computed here rather than written as an expression, so the filter gets a plain number.
        shifted = max(4000, min(96000, int(round(int(sample_rate) * pitch))))
        filters.append(f"asetrate={shifted}")
        filters.append(f"aresample={int(sample_rate)}")
        filters.append(_atempo(1.0 / pitch))
    if abs(rate - 1.0) >= 0.01:
        filters.append(_atempo(rate))
    try:
        proc = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
                               "-af", ",".join(filters), "-f", "wav", "pipe:1"],
                              input=data, capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return data
    return _wav(proc.stdout) if proc.returncode == 0 else data


# ---- cache: content-addressed WAV files, pruned by age once the folder grows past CACHE_MAX

_cache_lock = threading.Lock()
_cache_writes = 0


def _cache_key(cfg, text, voice, lang, sample_path, rate, pitch):
    h = hashlib.sha256()
    h.update(("|".join([str(cfg.get("style") or ""), str(cfg.get("base_url") or ""),
                        str(cfg.get("model") or ""), str(cfg.get("sample_rate") or ""),
                        voice, lang, "%.3f" % rate, "%.3f" % pitch, text])).encode("utf-8"))
    if sample_path and os.path.isfile(sample_path):
        with open(sample_path, "rb") as f:
            h.update(hashlib.sha256(f.read()).digest())
    return h.hexdigest()


def _cache_dir(data_dir):
    return os.path.join(data_dir, CACHE_DIR)


def _cache_get(data_dir, key):
    path = os.path.join(_cache_dir(data_dir), key + ".wav")
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def _prune(data_dir):
    folder = _cache_dir(data_dir)
    try:
        entries = [(e.stat().st_mtime, e.path) for e in os.scandir(folder) if e.is_file()]
    except OSError:
        return
    total = sum(os.path.getsize(p) for _, p in entries if os.path.isfile(p))
    if total <= CACHE_MAX:
        return
    for _, path in sorted(entries):
        try:
            total -= os.path.getsize(path)
            os.remove(path)
        except OSError:
            pass
        if total <= CACHE_PRUNE:
            break


def _cache_put(data_dir, key, data):
    global _cache_writes
    try:
        _write_bytes(os.path.join(_cache_dir(data_dir), key + ".wav"), data)
    except OSError:
        return
    with _cache_lock:
        _cache_writes += 1
        if _cache_writes % 40 == 0:
            _prune(data_dir)


def _split_text(text, limit=CHUNK_CHARS):
    # Break a long reply at paragraph, sentence or clause ends, so each part stays under the model's limit.
    parts = []
    rest = text
    while len(rest) > limit:
        cut = -1
        for sep in ("\n\n", "\n", ". ", "! ", "? ", "; ", ", ", " "):
            at = rest.rfind(sep, 0, limit)
            if at > limit // 3:
                cut = at + len(sep)
                break
        if cut < 0:
            cut = limit
        parts.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    if rest:
        parts.append(rest)
    return [p for p in parts if p]


def _join_wav(parts):
    # WAV files from the same server share a format, so the frames can be written into one file.
    if len(parts) == 1:
        return parts[0]
    params = None
    frames = []
    for data in parts:
        with wave.open(io.BytesIO(data), "rb") as w:
            p = w.getparams()
            if params is None:
                params = p
            elif (p.nchannels, p.sampwidth, p.framerate) != (params.nchannels, params.sampwidth, params.framerate):
                raise VoiceError("The speech server changed its audio format between parts.")
            frames.append(w.readframes(w.getnframes()))
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setparams(params)
        for frame in frames:
            w.writeframes(frame)
    return out.getvalue()


def speak(data_dir, cfg, text, voice, lang, sample_path, rate=1.0, pitch=1.0, dialogue_only=False,
          strict_dialogue=False):
    # Cached audio for one cleaned line. The cache key covers every input that changes the sound.
    text = clean_text(text, dialogue_only=dialogue_only, strict_dialogue=strict_dialogue)
    if not text:
        raise VoiceError("There is nothing to read.")
    voice = voice if voice in VOICES else (cfg.get("voice") or "female")
    lang = lang if lang in LANGS else (cfg.get("lang") or "en")
    rate = _clamp(rate, 0.5, 2.0, 1.0)
    pitch = _clamp(pitch, 0.5, 2.0, 1.0)
    key = _cache_key(cfg, text, voice, lang, sample_path, rate, pitch)
    hit = _cache_get(data_dir, key)
    if hit is not None:
        return hit
    if len(text) <= CHUNK_CHARS:
        data = synthesize(cfg, text, voice, lang, sample_path, rate, pitch)
    else:
        parts = [synthesize(cfg, part, voice, lang, sample_path, rate, pitch) for part in _split_text(text)]
        data = _join_wav(parts)
    _cache_put(data_dir, key, data)
    return data


# ---- character defaults, stored beside the character card

def character_folder(folder):
    return os.path.join(folder, VOICE_DIR)


def load_character(folder):
    data = _read_json(os.path.join(character_folder(folder), CHAR_FILE), CHAR_DEFAULTS)
    voice = str(data.get("voice") or "")
    lang = str(data.get("lang") or "")
    sample = SAMPLE_NAME if os.path.isfile(os.path.join(character_folder(folder), SAMPLE_NAME)) else ""
    return {"voice": voice if voice in VOICES else "", "lang": lang if lang in LANGS else "", "sample": sample}


def save_character(folder, patch):
    current = load_character(folder)
    voice = str(patch.get("voice", current["voice"]) or "")
    lang = str(patch.get("lang", current["lang"]) or "")
    if voice and voice not in VOICES:
        raise VoiceError("Choose a female or male voice.")
    if lang and lang not in LANGS:
        raise VoiceError("That speech language is not supported.")
    data = {"voice": voice, "lang": lang}
    _write_json(os.path.join(character_folder(folder), CHAR_FILE), data)
    return load_character(folder)


def save_character_sample(folder, data):
    wav = convert_sample(data)
    _write_bytes(os.path.join(character_folder(folder), SAMPLE_NAME), wav)
    return load_character(folder)


def remove_character_sample(folder):
    path = os.path.join(character_folder(folder), SAMPLE_NAME)
    try:
        os.remove(path)
    except OSError:
        pass
    return load_character(folder)


def character_sample_path(folder):
    path = os.path.join(character_folder(folder), SAMPLE_NAME)
    return path if os.path.isfile(path) else None


# ---- account preferences and profiles

def user_root(data_dir, uid):
    return os.path.join(data_dir, "users", str(uid), VOICE_DIR)


def _user_path(data_dir, uid):
    return os.path.join(user_root(data_dir, uid), USER_FILE)


def load_user(data_dir, uid):
    raw = _read_json(_user_path(data_dir, uid), USER_DEFAULTS)
    out = dict(USER_DEFAULTS)
    out["auto"] = bool(raw.get("auto"))
    out["rate"] = _clamp(raw.get("rate"), 0.5, 2.0, 1.0)
    out["pitch"] = _clamp(raw.get("pitch"), 0.5, 2.0, 1.0)
    out["volume"] = _clamp(raw.get("volume"), 0.0, 1.0, 1.0)
    out["dialogue_only"] = bool(raw.get("dialogue_only"))
    out["choice"] = str(raw.get("choice") or "character")
    out["charChoices"] = {str(k): str(v) for k, v in (raw.get("charChoices") or {}).items()
                          if isinstance(v, str)}
    out["profiles"] = {}
    for pid, prof in (raw.get("profiles") or {}).items():
        if not PROFILE_ID_RE.match(str(pid)) or not isinstance(prof, dict):
            continue
        voice = str(prof.get("voice") or "")
        lang = str(prof.get("lang") or "")
        out["profiles"][pid] = {"id": pid, "name": str(prof.get("name") or "Voice")[:40],
                                "voice": voice if voice in VOICES else "",
                                "lang": lang if lang in LANGS else "",
                                "sample": bool(prof.get("sample")) and os.path.isfile(profile_sample_path(data_dir, uid, pid))}
    # Older saves could pick the browser voice. It is gone, so those choices fall back to the character's own voice.
    if out["choice"] != "character" and out["choice"] not in out["profiles"]:
        out["choice"] = "character"
    out["charChoices"] = {k: v for k, v in out["charChoices"].items()
                          if v == "character" or v in out["profiles"]}
    return out


def save_user(data_dir, uid, prefs):
    _write_json(_user_path(data_dir, uid), prefs)
    return prefs


def update_user(data_dir, uid, patch):
    prefs = load_user(data_dir, uid)
    if "auto" in patch:
        prefs["auto"] = bool(patch["auto"])
    for key, lo, hi in (("rate", 0.5, 2.0), ("pitch", 0.5, 2.0), ("volume", 0.0, 1.0)):
        if key in patch:
            prefs[key] = _clamp(patch[key], lo, hi, prefs[key])
    if "dialogue_only" in patch:
        prefs["dialogue_only"] = bool(patch["dialogue_only"])
    if "choice" in patch:
        choice = str(patch["choice"] or "character")
        if choice == "browser":
            choice = "character"
        if choice != "character" and choice not in prefs["profiles"]:
            raise VoiceError("No such voice profile.")
        folder = folder_for(str(patch.get("character") or "")) if patch.get("character") else ""
        if folder:
            # The settings panel picks a voice for the open chat, so it is kept per character.
            if choice == "character":
                prefs["charChoices"].pop(folder, None)
            else:
                prefs["charChoices"][folder] = choice
        else:
            prefs["choice"] = choice
    return save_user(data_dir, uid, prefs)


def profile_sample_path(data_dir, uid, pid):
    if not PROFILE_ID_RE.match(str(pid)):
        return None
    path = os.path.join(user_root(data_dir, uid), SAMPLES_DIR, pid + ".wav")
    return path if os.path.isfile(path) else None


def upsert_profile(data_dir, uid, payload):
    prefs = load_user(data_dir, uid)
    pid = str(payload.get("id") or "")
    if pid and pid not in prefs["profiles"]:
        raise VoiceError("No such voice profile.")
    if not pid:
        pid = secrets.token_hex(6)
        while pid in prefs["profiles"]:
            pid = secrets.token_hex(6)
    name = str(payload.get("name") or "").strip()[:40] or "Voice"
    voice = str(payload.get("voice") or "")
    lang = str(payload.get("lang") or "")
    if voice and voice not in VOICES:
        raise VoiceError("Choose a female or male voice.")
    if lang and lang not in LANGS:
        raise VoiceError("That speech language is not supported.")
    if len(prefs["profiles"]) >= 20 and not pid:
        raise VoiceError("An account can keep up to 20 voice profiles.")
    prefs["profiles"][pid] = {"id": pid, "name": name, "voice": voice, "lang": lang,
                              "sample": os.path.isfile(os.path.join(user_root(data_dir, uid), SAMPLES_DIR, pid + ".wav"))}
    save_user(data_dir, uid, prefs)
    return prefs["profiles"][pid]


def save_profile_sample(data_dir, uid, pid, data):
    prefs = load_user(data_dir, uid)
    if pid not in prefs["profiles"]:
        raise VoiceError("No such voice profile.")
    wav = convert_sample(data)
    _write_bytes(os.path.join(user_root(data_dir, uid), SAMPLES_DIR, pid + ".wav"), wav)
    prefs["profiles"][pid]["sample"] = True
    save_user(data_dir, uid, prefs)
    return prefs["profiles"][pid]


def remove_profile(data_dir, uid, pid):
    prefs = load_user(data_dir, uid)
    if pid not in prefs["profiles"]:
        raise VoiceError("No such voice profile.")
    prefs["profiles"].pop(pid, None)
    prefs["charChoices"] = {k: v for k, v in prefs["charChoices"].items() if v != pid}
    if prefs["choice"] == pid:
        prefs["choice"] = "character"
    save_user(data_dir, uid, prefs)
    try:
        os.remove(os.path.join(user_root(data_dir, uid), SAMPLES_DIR, pid + ".wav"))
    except OSError:
        pass


def resolve(prefs, folder, char_folder):
    # The voice a line should use. A per-character choice wins over the account default.
    choice = prefs["charChoices"].get(folder or "", prefs["choice"])
    if choice != "character" and choice in prefs["profiles"]:
        prof = prefs["profiles"][choice]
        return {"source": "profile", "profile": prof["id"], "name": prof["name"],
                "voice": prof["voice"], "lang": prof["lang"],
                "sample": None, "choice": choice}
    char = load_character(char_folder) if char_folder else {"voice": "", "lang": "", "sample": ""}
    return {"source": "character", "profile": "", "name": "",
            "voice": char["voice"], "lang": char["lang"], "sample": char["sample"] or None,
            "choice": "character"}


def public_profile(data_dir, uid, prof):
    return {"id": prof["id"], "name": prof["name"], "voice": prof["voice"], "lang": prof["lang"],
            "sample": bool(profile_sample_path(data_dir, uid, prof["id"]))}


def public_prefs(data_dir, uid, prefs):
    return {"auto": prefs["auto"], "rate": prefs["rate"],
            "pitch": prefs["pitch"], "volume": prefs["volume"],
            "dialogue_only": prefs["dialogue_only"], "choice": prefs["choice"],
            "charChoices": prefs["charChoices"],
            "profiles": [public_profile(data_dir, uid, p) for p in prefs["profiles"].values()]}
