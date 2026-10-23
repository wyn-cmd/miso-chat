# Scheduled check-ins: a character sends the user a message at a set time, or at a random time within a range,
# each day. The check is made by a thread inside the server (no cron, no outside scheduler), so a check-in only
# happens while the server is running. If the server is down at the chosen time, that day's check-in is skipped.
import datetime
import random

import pytz

# When this server process started. A check-in is only sent if the process was already running at its target time.
STARTED = datetime.datetime.now(datetime.timezone.utc)
# How often the thread looks at the schedules, in seconds.
TICK_SECONDS = 30
# How long after its target time a check-in can still be sent, so a slow tick does not lose it.
GRACE_SECONDS = 120


# The user's time zone by name. An unknown or empty name falls back to the server's own local time zone.
def zone_for(name):
    try:
        return pytz.timezone(name)
    except (pytz.UnknownTimeZoneError, ValueError, TypeError):
        return datetime.datetime.now().astimezone().tzinfo


# The local date-time of a minute of the day on a given date in a zone (pytz zones need localize).
def at_local(tz, day, minute):
    naive = datetime.datetime(day.year, day.month, day.day, minute // 60, minute % 60)
    return tz.localize(naive) if hasattr(tz, "localize") else naive.replace(tzinfo=tz)


# The minute of the day a check-in is aimed at today: the set time, or a random minute in the range.
def pick_minute(row):
    if row["mode"] == "random":
        return random.randint(min(row["start_min"], row["end_min"]), max(row["start_min"], row["end_min"]))
    return row["at_min"]


# Looks at every enabled check-in once. For each one: picks today's target time if it has none yet, then sends
# the check-in when the target has come and the process was running at the time. fire(uid, character) makes
# the message and returns True when it was saved. Each row is marked as fired (or skipped) for the day before
# anything is generated, so a check-in is never sent twice.
def tick(store, fire, now=None, log=lambda m: None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    for row in store.checkin_enabled_rows():
        uid, name = row["user_id"], row["character"]
        tz = zone_for(row["tz"])
        local = now.astimezone(tz)
        today = local.strftime("%Y-%m-%d")
        if row["target_day"] != today:
            minute = pick_minute(row)
            store.checkin_update(uid, name, target_day=today, target_min=minute)
            row["target_day"], row["target_min"] = today, minute
        if row["fired_day"] == today:
            continue
        target = at_local(tz, local.date(), row["target_min"])
        if local < target:
            continue
        store.checkin_update(uid, name, fired_day=today)
        late = (now - target).total_seconds()
        if late > GRACE_SECONDS or STARTED > target.astimezone(datetime.timezone.utc):
            log(f"check-in for {name} (user {uid}) skipped: the server was not running at {target:%H:%M}")
            continue
        try:
            if fire(uid, name):
                log(f"check-in sent: {name} for user {uid} at {target:%H:%M}")
        except Exception as err:   # one failed check-in must not stop the others
            log(f"check-in for {name} (user {uid}) failed: {err}")


# Sets the schedule for a check-in from the settings page. The day's target and fired mark are cleared, so the
# new time applies from today on, and a time still to come today is not blocked by an earlier skip.
def save_settings(store, uid, name, enabled, mode, at_min, start_min, end_min, tz):
    store.checkin_update(uid, name, enabled=int(bool(enabled)), mode=mode, at_min=at_min, start_min=start_min,
                         end_min=end_min, tz=tz, target_day="", target_min=-1, fired_day="")


# The settings as the page shows them: times as HH:MM, and the next target when there is one today.
def view(row, unread=0):
    def hhmm(m):
        return f"{m // 60:02d}:{m % 60:02d}"
    if row is None:
        return {"enabled": False, "mode": "at", "at": "20:00", "start": "09:00", "end": "19:00", "tz": "",
                "unread": 0, "next": None}
    return {"enabled": bool(row["enabled"]), "mode": row["mode"], "at": hhmm(row["at_min"]),
            "start": hhmm(row["start_min"]), "end": hhmm(row["end_min"]), "tz": row["tz"], "unread": unread,
            "next": hhmm(row["target_min"]) if row["target_day"] and row["target_min"] >= 0 else None}


# Reads "HH:MM" into minutes after midnight, or None when it is not a time.
def parse_hhmm(text):
    try:
        hours, minutes = str(text).split(":")
        h, m = int(hours), int(minutes)
    except (ValueError, AttributeError):
        return None
    return h * 60 + m if 0 <= h <= 23 and 0 <= m <= 59 else None
