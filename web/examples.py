# Example messages: a few exchanges that show how a character speaks, saved beside the card and put in the system
# prompt so the model copies the voice rather than inferring it from the persona description alone. Nothing here calls
# a model.
#
# The file is examples.json in the character folder, so it travels with the character through export and import. Each
# exchange is one message from the user and the character's answer to it. The pairs are reference material, never part
# of the chat: they are labelled as examples and the model is told not to repeat them.

import json
import os

FILE = "examples.json"
MAX_EXAMPLES = 12          # exchanges per character
USER_MAX = 1000            # characters of one user message
REPLY_MAX = 2000           # characters of one character reply
BLOCK_MAX = 6000           # characters of the whole prompt block, so a full set cannot crowd the card


class ExamplesError(ValueError):
    pass


# A clean exchange, or None when both halves are blank (a row the admin added and then left empty).
def _pair(raw, number):
    if not isinstance(raw, dict):
        raise ExamplesError(f"Example {number} must have a user message and a reply.")
    user = str(raw.get("user") or "").strip()
    reply = str(raw.get("character") or "").strip()
    if not user and not reply:
        return None
    if not user or not reply:
        raise ExamplesError(f"Example {number} needs both the user's message and the character's reply.")
    if len(user) > USER_MAX:
        raise ExamplesError(f"The user's message in example {number} must be {USER_MAX} characters or fewer.")
    if len(reply) > REPLY_MAX:
        raise ExamplesError(f"The reply in example {number} must be {REPLY_MAX} characters or fewer.")
    return {"user": user, "character": reply}


# A whole list of exchanges. Raises ExamplesError for the first problem found.
def validate(raw):
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ExamplesError("Example messages must be a list.")
    out = []
    for i, item in enumerate(raw, start=1):
        pair = _pair(item, i)
        if pair is not None:
            out.append(pair)
    if len(out) > MAX_EXAMPLES:
        raise ExamplesError(f"A character can have up to {MAX_EXAMPLES} example messages.")
    return out


# The saved exchanges, or an empty list. A damaged file means no examples, never a broken chat.
def load(folder):
    path = os.path.join(folder, FILE)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    raw = data.get("examples") if isinstance(data, dict) else data
    try:
        return validate(raw)
    except ExamplesError:
        return []


# Writes the exchanges, or removes the file when the list is empty. Returns what was stored.
def save(folder, raw):
    pairs = validate(raw)
    path = os.path.join(folder, FILE)
    if not pairs:
        if os.path.exists(path):
            os.remove(path)
        return []
    os.makedirs(folder, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"examples": pairs}, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return pairs


# The prompt block, or "" when there are no examples. name is the character's display name, used to label its own
# lines; the user's lines are labelled User, because the account's persona is only known per turn.
def prompt_block(pairs, name):
    if not pairs:
        return ""
    lines = ["--- Example messages: how you speak and reply (voice reference only; never repeat these lines) ---"]
    user_label = "User"
    for pair in pairs:
        lines.append(f"{user_label}: {pair['user']}")
        lines.append(f"{name}: {pair['character']}")
        if len("\n".join(lines)) > BLOCK_MAX:
            break
    return "\n".join(lines)[:BLOCK_MAX]
