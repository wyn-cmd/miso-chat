# Provider settings for the web app: DeepSeek key, local LLM endpoint and key, Claude Code location, and the
# speech server that reads replies aloud.
#
# Stored as providers.json in the data folder (outside the character tree and outside git). Keys are
# written with owner-only permissions where the OS supports it, and are never returned to the browser:
# the admin screen sees only a masked form such as 'sk-...a1b2'.
import json
import os
import shutil

PROVIDER_FILE = "providers.json"
DEFAULTS = {
    "deepseek": {"enabled": False, "api_key": ""},
    "local": {"enabled": False, "base_url": "http://127.0.0.1:8000/v1", "api_key": "", "models": ""},
    "claude": {"enabled": False, "exe": ""},
    # A llama.cpp server started with an audio model, or any server with an OpenAI-compatible speech endpoint.
    # style "llama" posts to /tts and accepts a reference clip; style "openai" posts to /v1/audio/speech.
    "audio": {"enabled": False, "base_url": "http://127.0.0.1:8080", "api_key": "", "style": "llama",
              "model": "", "lang": "en", "voice": "female", "timeout": 60, "sample_rate": 24000},
}


def _path(data_dir):
    return os.path.join(data_dir, PROVIDER_FILE)


def load(data_dir):
    out = json.loads(json.dumps(DEFAULTS))
    try:
        with open(_path(data_dir), encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, ValueError):
        return out
    for name, fields in DEFAULTS.items():
        for key in fields:
            if isinstance(saved.get(name), dict) and key in saved[name]:
                out[name][key] = saved[name][key]
    return out


def save(data_dir, cfg):
    os.makedirs(data_dir, exist_ok=True)
    tmp = _path(data_dir) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, _path(data_dir))


def mask(secret):
    if not secret:
        return ""
    if len(secret) <= 8:
        return "set"
    return secret[:3] + "..." + secret[-4:]


def public_view(cfg):
    # What the browser may see: enabled flags and masked keys, never a key itself.
    return {
        "deepseek": {"enabled": bool(cfg["deepseek"]["enabled"]), "key_set": bool(cfg["deepseek"]["api_key"]),
                     "key_hint": mask(cfg["deepseek"]["api_key"])},
        "local": {"enabled": bool(cfg["local"]["enabled"]), "base_url": cfg["local"]["base_url"],
                  "key_set": bool(cfg["local"]["api_key"]), "key_hint": mask(cfg["local"]["api_key"]),
                  "models": cfg["local"]["models"]},
        "claude": {"enabled": bool(cfg["claude"]["enabled"]), "exe": cfg["claude"]["exe"],
                   "found": bool(shutil.which(cfg["claude"]["exe"] or "claude"))},
        "audio": {"enabled": bool(cfg["audio"]["enabled"]), "base_url": cfg["audio"]["base_url"],
                  "key_set": bool(cfg["audio"]["api_key"]), "key_hint": mask(cfg["audio"]["api_key"]),
                  "style": cfg["audio"]["style"], "model": cfg["audio"]["model"],
                  "lang": cfg["audio"]["lang"], "voice": cfg["audio"]["voice"],
                  "timeout": cfg["audio"]["timeout"], "sample_rate": cfg["audio"]["sample_rate"]},
    }


def update(data_dir, patch):
    # Apply an admin patch. A key field is only changed when a non-empty value is sent; blank keeps it.
    cfg = load(data_dir)
    for name, fields in patch.items():
        if name not in DEFAULTS or not isinstance(fields, dict):
            continue
        for key, val in fields.items():
            if key not in DEFAULTS[name]:
                continue
            if key in ("api_key",) and (val is None or val == ""):
                continue
            if key == "enabled":
                cfg[name][key] = bool(val)
            elif key == "style":
                cfg[name][key] = "openai" if str(val).strip().lower() == "openai" else "llama"
            elif key == "voice":
                cfg[name][key] = "male" if str(val).strip().lower() == "male" else "female"
            elif key in ("timeout", "sample_rate"):
                low, high = (1, 600) if key == "timeout" else (8000, 48000)
                try:
                    cfg[name][key] = min(high, max(low, int(val)))
                except (TypeError, ValueError):
                    pass
            else:
                cfg[name][key] = str(val).strip()
    save(data_dir, cfg)
    return cfg
