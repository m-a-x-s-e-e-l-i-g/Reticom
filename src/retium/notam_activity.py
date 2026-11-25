"""Local activity classification and a bounded UTC schedule window for the map."""

import math
import re
from datetime import datetime, timedelta, timezone


NOTAM_LOOKAHEAD = 3600
NOTAM_FETCH_LOOKAHEAD = 5400
LOCAL_ACTIVITY_SPAN_NM = 20
ACTIVITIES = {
    "parachuting": ("WP", r"\b(?:PARACHUT\w*|SKYDIV\w*|PJE|PARAGLID\w*|HANG[ -]?GLID\w*)\b"),
    "drones": ("WU", r"\b(?:UAS|UAV|RPAS|DRONES?|UNMANNED\s+(?:ACFT|AIRCRAFT|AERIAL))\b"),
    "fireworks": (None, r"\b(?:FIRE\s?WORKS?|PYROTECHNIC\w*)\b"),
    "model": ("WZ", r"\bMODEL\s+(?:AIRCRAFT|ACFT|FLYING)\b"),
    "airshow": ("WA", r"\b(?:AIR[ -]?(?:SHOW|DISPLAY)S?|AEROBATIC DISPLAY)\b"),
}
WEEKDAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
WEEKDAY_PATTERN = "|".join(WEEKDAYS)
MONTH_PATTERN = "|".join(MONTHS)
SLOT = r"(?:[0-9]{4}-[0-9]{4}|H24)"
SCHEDULE_GROUP = re.compile(
    rf"(?:(DAILY|DLY|(?:{WEEKDAY_PATTERN})(?:[ -](?:{WEEKDAY_PATTERN}))*|"
    rf"(?:{MONTH_PATTERN}) [0-9]{{1,2}}(?:[ -][0-9]{{1,2}})*) )?"
    rf"({SLOT}(?: {SLOT})*)(?: |$)"
)


def local_activity(selection_code, category, text, kind, bounds, radius):
    code = re.fullmatch(r"Q?(W[A-Z])(?:[A-Z]{2})?", str(selection_code or "").upper())
    subject = code[1] if code else None
    if kind == "Point":
        if radius is not None:
            try:
                if not 0 <= float(radius) <= LOCAL_ACTIVITY_SPAN_NM / 2:
                    return ""
            except (TypeError, ValueError, OverflowError):
                return ""
    else:
        west, south, east, north = bounds
        width = (east - west) * 60 * math.cos(math.radians((south + north) / 2))
        if max(width, (north - south) * 60) > LOCAL_ACTIVITY_SPAN_NM:
            return ""
    for activity, (code, _) in ACTIVITIES.items():
        if code and subject == code:
            return activity
    if category in {"airspace", "warning", "other"}:
        for activity, (_, pattern) in ACTIVITIES.items():
            if re.search(pattern, text, re.IGNORECASE):
                return activity
    return ""


def utc_timestamp(value):
    if not value:
        return None
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return date.timestamp() if date.tzinfo is not None else None
    except (ValueError, OverflowError, OSError):
        return None


def _range_tokens(values, convert):
    selected = set()
    for token in values.split():
        first, sep, last = token.partition("-")
        start = convert(first)
        stop = convert(last) if sep else start
        if start > stop:
            raise ValueError
        selected.update(range(start, stop + 1))
    return selected


def _schedule_days(selector, day, start_date):
    if selector in ("DAILY", "DLY"):
        return True
    if not selector:
        return day.date() == start_date.date()
    first, _, rest = selector.partition(" ")
    if first in MONTHS:
        return day.month == MONTHS.index(first) + 1 and day.day in _range_tokens(rest, int)
    return day.weekday() in _range_tokens(selector, WEEKDAYS.index)


def _minutes(hhmm):
    hour, minute = int(hhmm[:2]), int(hhmm[2:])
    if hour > 24 or minute > 59 or hour == 24 and minute:
        raise ValueError
    return hour * 60 + minute


def activity_periods(schedule, start, end, now):
    """Return UTC epoch intervals. None means the supplied schedule could not be verified."""
    if not schedule or schedule.upper() in {"H24", "CONTINUOUS"}:
        return [[start, end]]
    # shortcut: exact UTC times and simple day selectors, expand when more Item D formats can be verified.
    text = " ".join(schedule.upper().translate(str.maketrans({"–": "-", "—": "-", ",": " ", ";": " "})).split())
    fields = []
    while text:
        match = SCHEDULE_GROUP.match(text)
        if not match:
            return None
        fields.append(match.groups())
        text = text[match.end():]
    midnight = datetime.fromtimestamp(now, timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    start_date = datetime.fromtimestamp(start, timezone.utc)
    intervals = []
    try:
        for offset in (-1, 0, 1):
            day = midnight + timedelta(days=offset)
            for selector, slots in fields:
                if not _schedule_days(selector, day, start_date):
                    continue
                for slot in slots.split():
                    a, b = (0, 1440) if slot == "H24" else map(_minutes, slot.split("-"))
                    if a == 1440 or a == b:
                        raise ValueError
                    if b < a:
                        b += 1440
                    begins = max(start, day.timestamp() + a * 60)
                    ends = day.timestamp() + b * 60
                    if end is not None:
                        ends = min(end, ends)
                    if begins < ends and ends > now and begins <= now + NOTAM_FETCH_LOOKAHEAD:
                        intervals.append([begins, ends])
    except ValueError:
        return None
    return sorted(intervals)
