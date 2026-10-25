# Imports a SillyTavern-style character card (a JSON file, or a PNG with the card in a text chunk) into the
# new-character form. Nothing is written here: the result fills the form for the admin to check, and the
# picture and the lorebook are kept until the character is created.
#
# Accepted: the v1 layout (fields at the top level), the v2 layout ("spec": "chara_card_v2") and v3
# ("chara_card_v3"). In a PNG, the "ccv3" text chunk is read first, then "chara".
import base64
import json
import re
import struct
import zlib

from web import palace, profiles, examples

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_TEXT = 2_000_000        # largest card text accepted, after any decompression
CARD_MAX = 20000            # the editor's limit for the card and the context
SELECTIVE_LOGIC = {0: "and_any", 1: "not_all", 2: "not_any", 3: "and_all"}


class CardError(ValueError):
    pass


# Inflates one compressed text chunk, refusing anything that would expand past MAX_TEXT.
def _inflate(raw):
    d = zlib.decompressobj()
    out = d.decompress(raw, MAX_TEXT)
    if d.unconsumed_tail:
        raise CardError("The card text is too large.")
    return out


# Every text chunk in a PNG as {keyword: text}. Reads tEXt, zTXt and iTXt; other chunks are skipped.
def _png_texts(data):
    if not data.startswith(PNG_SIGNATURE):
        raise CardError("That PNG file is damaged.")
    out, pos = {}, len(PNG_SIGNATURE)
    while pos + 8 <= len(data):
        length, ctype = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if ctype == b"IEND":
            break
        if ctype == b"tEXt":
            key, _, text = body.partition(b"\0")
            out[key.decode("latin-1")] = text.decode("latin-1")
        elif ctype == b"zTXt":
            key, _, rest = body.partition(b"\0")
            if rest[:1] == b"\0":
                out[key.decode("latin-1")] = _inflate(rest[1:]).decode("latin-1")
        elif ctype == b"iTXt":
            key, _, rest = body.partition(b"\0")
            compressed, rest = rest[:1], rest[2:]
            _, _, rest = rest.partition(b"\0")      # language tag
            _, _, text = rest.partition(b"\0")      # translated keyword
            if compressed == b"\x01":
                text = _inflate(text)
            out[key.decode("latin-1")] = text.decode("utf-8", errors="replace")
    return out


# The card object from an uploaded file, by its name and content.
def read_card(filename, data):
    if len(data) > MAX_TEXT:
        raise CardError("The card file is too large.")
    if data.startswith(PNG_SIGNATURE):
        texts = _png_texts(data)
        for key in ("ccv3", "chara"):
            if key in texts:
                try:
                    return json.loads(base64.b64decode(texts[key]).decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    raise CardError("The card in this PNG could not be read.") from None
        raise CardError("This PNG has no character card in it.")
    try:
        card = json.loads(data.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError):
        raise CardError("That file is not a character card (JSON or PNG).") from None
    if not isinstance(card, dict):
        raise CardError("That file is not a character card.")
    return card


# The card fields, whichever layout they are in. v2 and v3 keep them under "data".
def _fields(card):
    if card.get("spec") in ("chara_card_v2", "chara_card_v3") or isinstance(card.get("data"), dict):
        inner = card.get("data")
        return inner if isinstance(inner, dict) else {}
    return card


# Text from a field, with a single string as-is and anything else left out.
def _text(value):
    return value.strip() if isinstance(value, str) else ""


# SillyTavern macros: {{char}} is the character, {{user}} is the person chatting.
def _macros(text, name):
    text = re.sub(r"\{\{\s*char\s*\}\}", name, text, flags=re.I)
    # "The user" opens a line or a sentence; "the user" follows other words.
    def user_word(m):
        lead = m.group(1)
        return lead + "The user" if lead is not None else "the user"

    text = re.sub(r"(^|(?<=[\n.!?])\s*)\{\{\s*user\s*\}\}", lambda m: user_word(m), text, flags=re.I | re.M)
    return re.sub(r"\{\{\s*user\s*\}\}", "the user", text, flags=re.I)


# A line that names the speaker in example dialogue, as SillyTavern cards write it.
SPEAKER = re.compile(r"^\s*\{\{\s*(char|user)\s*\}\}\s*:\s*", re.I)


# The card's example dialogue as pairs of a user message and the character's reply. <START> separates exchanges, and
# a line beginning with {{char}}: or {{user}}: names the speaker; its following lines belong to it. A second line from
# the character with no user line between them continues the same reply rather than being dropped.
def _example_pairs(text, name):
    pairs = []
    for block in re.split(r"<START>", text or "", flags=re.I):
        turns = []   # [role, said] in the order they appear in the block
        for line in block.splitlines():
            m = SPEAKER.match(line)
            if m:
                turns.append([m.group(1).lower(), line[m.end():].strip()])
            elif turns and line.strip():
                turns[-1][1] = (turns[-1][1] + " " + line.strip()).strip()
        user = ""
        for role, said in turns:
            if role == "user":
                user = (user + " " + said).strip() if user else said
            elif not said:
                continue
            elif user:
                pairs.append({"user": user, "character": said})
                user = ""
            elif pairs:
                pairs[-1]["character"] = (pairs[-1]["character"] + " " + said).strip()
        if len(pairs) >= examples.MAX_EXAMPLES:
            break
    return [{"user": _macros(pair["user"], name)[:examples.USER_MAX],
             "character": _macros(pair["character"], name)[:examples.REPLY_MAX]}
            for pair in pairs[:examples.MAX_EXAMPLES]]


# The first sentence of the description, for the public headline.
def _headline(description):
    first = re.split(r"(?<=[.!?])\s", description, maxsplit=1)[0]
    return first[:profiles.HEADLINE_MAX].strip()


# Tags as lower-case words, without repeats, within the limits the profile checks.
def _tags(value):
    out = []
    if isinstance(value, list):
        for tag in value:
            tag = _text(tag).lower()[:profiles.TAG_MAX]
            if tag and tag not in out:
                out.append(tag)
    return out[:profiles.TAGS_MAX]


# A lorebook entry as a Mind Palace entry. Entries the palace cannot hold are counted and left out.
def _palace_entry(entry, name):
    keys = [k for k in (entry.get("keys") or []) if isinstance(k, str) and k.strip()]
    secondary = [k for k in (entry.get("secondary_keys") or []) if isinstance(k, str) and k.strip()]
    title = _text(entry.get("comment")) or (keys[0] if keys else "Entry")
    order = entry.get("insertion_order", entry.get("order", 100))
    try:
        order = min(1000, max(0, int(order)))
    except (TypeError, ValueError):
        order = 100
    return {
        "title": title[:palace.TITLE_MAX],
        "keys": keys,
        "secondary": secondary,
        "logic": SELECTIVE_LOGIC.get(entry.get("selectiveLogic", 0), "and_any"),
        "content": _macros(_text(entry.get("content")), name)[:palace.CONTENT_MAX],
        "constant": bool(entry.get("constant")),
        "enabled": entry.get("enabled", True) is not False,
        "whole": True,
        "depth": palace.DEFAULT_DEPTH,
        "order": order,
    }


# Turns a card object into the new-character form's values, and the notes the admin should read.
def to_draft(card, filename=""):
    f = _fields(card)
    notes = []
    name = _text(f.get("name")) or _text(card.get("name"))
    if not name:
        raise CardError("The card has no name.")
    description = _macros(_text(f.get("description")), name)
    personality = _macros(_text(f.get("personality")), name)
    scenario = _macros(_text(f.get("scenario")), name)
    examples_raw = _text(f.get("mes_example"))
    example_pairs = _example_pairs(examples_raw, name)
    instructions = _macros(_text(f.get("system_prompt")), name)

    parts = [f"Character Persona: {name}"]
    if description:
        parts.append(description)
    if personality:
        parts.append("Personality: " + personality)
    # Example dialogue goes to the editor's own example section when it can be read as exchanges. When it cannot,
    # it stays in the card, so nothing the card carried is lost.
    if examples_raw and not example_pairs:
        parts.append("Example dialogue:\n" + _macros(examples_raw, name).replace("<START>", "").strip())
    if instructions:
        parts.append("Instructions:\n" + instructions)
        notes.append("The card's system prompt is in the card text under Instructions. Check it before you create.")
    card_text = "\n\n".join(parts)
    if len(card_text) > CARD_MAX:
        card_text = card_text[:CARD_MAX]
        notes.append("The card text was longer than the editor allows and has been cut short.")

    context = scenario[:CARD_MAX]
    if len(scenario) > CARD_MAX:
        notes.append("The scenario was cut short to fit the context box.")

    creator_notes = _text(f.get("creator_notes"))
    info = _macros(creator_notes, name)[:profiles.INFO_MAX]
    if creator_notes:
        notes.append("The creator's notes became the public summary. Check it does not reveal anything hidden.")

    if example_pairs:
        notes.append(f"{len(example_pairs)} example messages were read into the example section. Check them.")
    if _text(f.get("first_mes")) or any(_text(g) for g in (f.get("alternate_greetings") or []) if isinstance(g, str)):
        notes.append("Opening messages were not imported: Miso has no greeting message.")

    entries = []
    book = f.get("character_book")
    raw_entries = book.get("entries") if isinstance(book, dict) else None
    if isinstance(raw_entries, dict):
        raw_entries = list(raw_entries.values())
    skipped = 0
    for entry in raw_entries or []:
        if not isinstance(entry, dict):
            skipped += 1
            continue
        item = _palace_entry(entry, name)
        try:
            entries += palace.validate([item])
        except palace.PalaceError:
            skipped += 1
        if len(entries) >= palace.MAX_ENTRIES:
            notes.append(f"Only the first {palace.MAX_ENTRIES} lorebook entries were kept.")
            break
    if entries:
        notes.append(f"{len(entries)} lorebook entries were added to the Mind Palace.")
    if skipped:
        notes.append(f"{skipped} lorebook entries could not be used and were left out.")

    return {
        "name": name,
        "character": card_text,
        "context": context,
        "examples": example_pairs,
        "headline": _headline(description),
        "info": info,
        "tags": _tags(f.get("tags")),
        "palace": entries,
        "icon": filename.lower().endswith(".png"),
        "notes": notes,
    }


# The import from an uploaded file: the card read from it, and the draft for the form.
def from_file(filename, data):
    return to_draft(read_card(filename, data), filename)
