# Relationship speed: how quickly a character's trust and affection grow. A character's creator picks a value from
# 1.00 to 6.00, to two decimal places, for each score. The value is stored beside the card, never in the card text,
# and only the category it falls in is shown to users. Scale 2.00 is the normal rate, 1.00 is half, and 6.00 is
# five times.
import json
import math
import os

FILE = "relationship.json"
TRUST_DEFAULT = 3.0
AFFECTION_DEFAULT = 2.0
# Scale -> multiplier of the event's positive change. The points between the whole numbers are interpolated.
# Negative changes and trust decay are never multiplied.
ANCHORS = ((1.0, 0.5), (2.0, 1.0), (3.0, 2.0), (4.0, 3.0), (5.0, 4.0), (6.0, 5.0))
TRUST_BANDS = ((2.0, "Cautious"), (4.0, "Open"), (5.0, "Trusting"), (6.0, "Heart of gold"))
AFFECTION_BANDS = ((2.0, "Fond"), (4.0, "Attached"), (5.0, "Devoted"), (6.0, "Soulmate"))
# Every combination of the two categories, as the character's relationship type.
TYPES = {
    ("Cautious", "Fond"): "Hard to get",
    ("Cautious", "Attached"): "Slow burn",
    ("Cautious", "Devoted"): "Tsundere",
    ("Cautious", "Soulmate"): "Star-crossed",
    ("Open", "Fond"): "Easy company",
    ("Open", "Attached"): "Fast friends",
    ("Open", "Devoted"): "Devoted companion",
    ("Open", "Soulmate"): "Destined lover",
    ("Trusting", "Fond"): "Gentle companion",
    ("Trusting", "Attached"): "Confidant",
    ("Trusting", "Devoted"): "Loyal lover",
    ("Trusting", "Soulmate"): "Twin flame",
    ("Heart of gold", "Fond"): "Heart of gold",
    ("Heart of gold", "Attached"): "Open-hearted",
    ("Heart of gold", "Devoted"): "Adoring soul",
    ("Heart of gold", "Soulmate"): "Soulbound",
}


def clean_scale(value, default=2.0):
    # A stored or submitted scale, clamped to 1.00-6.00 and rounded to two decimals. Anything unreadable falls
    # back to the supplied default.
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    if not math.isfinite(number):
        number = float(default)
    return round(max(1.0, min(6.0, number)), 2)


def checked(values):
    # Validates an editor payload. Raises ValueError for a value outside 1.00-6.00.
    if not isinstance(values, dict):
        raise ValueError("The relationship settings must be an object.")
    out = {}
    for key, default in (("trust", TRUST_DEFAULT), ("affection", AFFECTION_DEFAULT)):
        raw = values.get(key, default)
        try:
            number = float(raw)
        except (TypeError, ValueError):
            raise ValueError("A relationship scale must be a number from 1.00 to 6.00.") from None
        if not math.isfinite(number) or not 1.0 <= number <= 6.0:
            raise ValueError("A relationship scale must be a number from 1.00 to 6.00.")
        out[key] = round(number, 2)
    return out


def multiplier(scale):
    # The rate at a scale, interpolated between the whole-number anchors.
    value = clean_scale(scale)
    for (x0, y0), (x1, y1) in zip(ANCHORS, ANCHORS[1:]):
        if value <= x1:
            return y0 + (value - x0) * (y1 - y0) / (x1 - x0)
    return ANCHORS[-1][1]


def _band(scale, bands):
    scale = clean_scale(scale)
    for top, label in bands:
        if scale <= top:
            return label
    return bands[-1][1]


def trust_category(scale):
    return _band(scale, TRUST_BANDS)


def affection_category(scale):
    return _band(scale, AFFECTION_BANDS)


def type_name(trust_scale, affection_scale):
    # The archetype for a pair of categories, such as Cautious and Fond -> Hard to get.
    return TYPES.get((trust_category(trust_scale), affection_category(affection_scale)), "Unwritten")


def read(folder):
    # The stored scales for one character folder. A missing or damaged file means the defaults.
    try:
        with open(os.path.join(folder, FILE), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    return {"trust": clean_scale(data.get("trust"), TRUST_DEFAULT),
            "affection": clean_scale(data.get("affection"), AFFECTION_DEFAULT)}


def save(folder, values):
    checked_values = checked(values)
    data = {"trust": checked_values["trust"], "affection": checked_values["affection"]}
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, FILE)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def labels(folder):
    # What a user may see: the categories and the archetype only, never the scale itself.
    values = read(folder)
    return {"trust": trust_category(values["trust"]), "affection": affection_category(values["affection"]),
            "type": type_name(values["trust"], values["affection"])}
