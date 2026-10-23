# Applies a memory refresh as changes to memory.md, instead of rewriting the whole file. The model replies with
# lines of three kinds: ADD (a new fact in a section), UPDATE (an existing line replaced) and REMOVE (an existing
# line dropped). A line that does not match an existing one is ignored, so a badly formed reply can add nothing
# or do nothing, but it cannot wipe the bank.
import re

LIMIT = 20000       # characters allowed in memory.md
BASELINE = "# Memory Bank\n\n## Relationships\nActive roleplay partnership.\n\n## Key Facts\n- Session is active and saved.\n"
SECTIONS = ("Locations", "Relationships", "Major Plot Points", "Key Facts", "Secrets / Hidden Motivations")
DEFAULT_SECTION = "Key Facts"
LINE = re.compile(r"^\s*(ADD|UPDATE|REMOVE)\s*:\s*(.*)$", re.I)
HEADER = re.compile(r"^#{1,3}\s+(.*)$")


# A line as compared with others: no bullet, no spacing, no case.
def _norm(line):
    return re.sub(r"^[-*+]\s*", "", line.strip()).strip().lower()


# The changes the model asked for, as (kind, payload) pairs. Anything else in the reply is ignored.
def parse(changes):
    out = []
    for raw in (changes or "").splitlines():
        found = LINE.match(raw)
        if found:
            out.append((found.group(1).upper(), found.group(2).strip()))
    return out


# The index of the header line for a section, or None. Matching ignores case and any "/" part of the name.
def _section_index(lines, section):
    want = section.split("/")[0].strip().lower()
    for i, line in enumerate(lines):
        found = HEADER.match(line)
        if found and found.group(1).split("/")[0].strip().lower() == want:
            return i
    return None


# Inserts a fact at the end of its section, or starts the section at the end of the bank when it is missing.
def _add(lines, section, fact):
    at = _section_index(lines, section)
    bullet = f"- {fact}"
    if at is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines += [f"## {section}", bullet]
        return lines
    end = at + 1
    while end < len(lines) and not HEADER.match(lines[end]):
        end += 1
    last = end
    while last > at + 1 and not lines[last - 1].strip():
        last -= 1
    lines.insert(last, bullet)
    return lines


# Applies a reply's changes to the bank text. Returns (new text, counts). Nothing is written past LIMIT.
def apply(existing, changes, limit=LIMIT):
    lines = (existing or BASELINE).rstrip("\n").split("\n")
    counts = {"added": 0, "updated": 0, "removed": 0}
    for kind, payload in parse(changes):
        if kind == "ADD":
            found = re.match(r"\[([^\]]+)\]\s*(.+)$", payload)
            section, fact = (found.group(1).strip(), found.group(2).strip()) if found else (DEFAULT_SECTION, payload)
            if not fact or any(_norm(line) == _norm(fact) for line in lines):
                continue
            trial = _add(list(lines), section, fact)
            if len("\n".join(trial)) > limit:
                continue
            lines = trial
            counts["added"] += 1
        elif kind == "UPDATE":
            old, sep, new = payload.partition("=>")
            old, new = old.strip(), new.strip()
            if not sep or not old or not new:
                continue
            hit = [i for i, line in enumerate(lines) if not HEADER.match(line) and _norm(line) == _norm(old)]
            if not hit:
                continue
            for i in hit:
                bullet = "- " if lines[i].lstrip().startswith(("-", "*", "+")) else ""
                lines[i] = bullet + new.lstrip("-*+ ").strip()
            counts["updated"] += len(hit)
        elif kind == "REMOVE":
            if not payload:
                continue
            # One line per REMOVE, so a duplicate can be removed without losing the fact it repeats.
            hit = next((i for i, line in enumerate(lines) if not HEADER.match(line) and _norm(line) == _norm(payload)), None)
            if hit is not None:
                del lines[hit]
                counts["removed"] += 1
    return "\n".join(lines).rstrip() + "\n", counts
