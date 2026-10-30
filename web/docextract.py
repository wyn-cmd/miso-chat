# Document import: an uploaded backstory, novel excerpt or setting document is read once and mined for
# candidate material: Mind Palace entries (title, keys, content) and short character facts for the memory
# bank. Nothing is saved here; the page shows the candidates for review, and only the ones the reader
# approves are applied (see the import routes in web/app.py).
import json
import re

SECTIONS = ("Locations", "Relationships", "Major Plot Points", "Key Facts", "Secrets / Hidden Motivations")
DEFAULT_SECTION = "Key Facts"
TEXT_LIMIT = 60000       # the most document text one extraction call reads
FACT_MAX = 250
CONTENT_MAX = 800
PALACE_CANDIDATES = 12   # most palace entries kept
FACT_CANDIDATES = 40     # most facts kept

SYSTEM = (
    "You mine a reference document about a roleplay setting for its most lasting facts. You are given the "
    "document text. Extract two kinds of material, and nothing else:\n"
    "1. Palace entries: stable lore a character would want ready. A place, family, faction, object, rule, "
    "magic system, organisation, backstory event or setting fact worth remembering as a block of lore.\n"
    "2. Character facts: short one-line facts suited to a memory bank, about named characters, their history, "
    "relationships, secrets or motives.\n"
    "Speak about the characters in the third person. Do not write scene direction, dialogue or requests. "
    "Reply with JSON only, and nothing outside it:\n"
    "{\"palace\": [{\"title\": \"short name\", \"keys\": [\"1-10 keywords that make the entry worth showing\", ...],"
    " \"content\": \"the lore, 1-4 plain sentences\"}, ...],\n"
    " \"facts\": [{\"section\": \"Locations, Relationships, Major Plot Points, Key Facts or Secrets / Hidden Motivations\","
    " \"text\": \"one fact, one line, stated as true\"}, ...]}\n"
    "Keys are words or short phrases that appear in chat when the entry should join the prompt; use names and "
    "distinctive nouns, never generic words like 'the' or 'room'. Keep each content under 800 characters and each "
    "fact under 250 characters. Up to 12 palace entries and up to 40 facts. Skip anything wordy, uncertain or not "
    "in the document. An empty list is fine when the document holds nothing of that kind."
)


# Pulls the JSON object out of a model reply: fenced or bare, with anything else around it ignored.
# Returns the JSON parsed, or None when no parsable object is found.
def extract_json(text):
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _one_line(value, limit):
    if not isinstance(value, str):
        return ""
    return " ".join(value.split()).strip()[:limit]


# Validates a parsed reply into two candidate lists: palace entries with title, keys and content, and
# memory facts with a section and one line of text. Malformed members are dropped, not kept half-formed.
def parse(text):
    data = extract_json(text)
    if data is None:
        return None
    palace_out = []
    for raw in (data.get("palace") or [])[:PALACE_CANDIDATES]:
        if not isinstance(raw, dict):
            continue
        title = _one_line(raw.get("title"), 80)
        content = str(raw.get("content") or "").strip()[:CONTENT_MAX]
        keys_raw = raw.get("keys")
        if isinstance(keys_raw, str):
            keys_raw = keys_raw.split(",")
        keys = []
        if isinstance(keys_raw, list):
            for key in keys_raw:
                key = _one_line(key, 60)
                if key and key.lower() not in [k.lower() for k in keys]:
                    keys.append(key)
        keys = keys[:10]
        if content and (keys or title):
            palace_out.append({"title": title, "keys": keys, "content": content})
    facts_out = []
    for raw in (data.get("facts") or [])[:FACT_CANDIDATES]:
        if not isinstance(raw, dict):
            continue
        section = _one_line(raw.get("section"), 60)
        match = next((s for s in SECTIONS if s.split("/")[0].strip().lower() in section.lower()), None)
        text_fact = _one_line(raw.get("text"), FACT_MAX).lstrip("-*+ ")
        if text_fact:
            facts_out.append({"section": match or DEFAULT_SECTION, "text": text_fact})
    return {"palace": palace_out, "facts": facts_out}
