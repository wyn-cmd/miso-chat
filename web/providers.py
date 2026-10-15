# Provider settings for the web app: DeepSeek key, local LLM endpoint and key, Claude Code location.
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
            elif key in ("base_url", "exe", "models", "api_key"):
                cfg[name][key] = str(val).strip()
    save(data_dir, cfg)
    return cfg
