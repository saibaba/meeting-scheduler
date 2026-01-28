from __future__ import annotations
import datetime as dt
import pytz
import dateparser
import re

def ensure_tz(tz_name: str) -> pytz.timezone:
    try:
        return pytz.timezone(tz_name)
    except Exception:
        return pytz.timezone("America/Los_Angeles")

def parse_natural_datetime(text: str, tz_name: str) -> dt.datetime | None:
    """
    Parse a natural language datetime using dateparser with a preferred timezone.
    Returns timezone-aware datetime, or None.
    """
    tz = ensure_tz(tz_name)
    settings = {
        "RETURN_AS_TIMEZONE_AWARE": True,
        "TIMEZONE": tz_name,
        "PREFER_DATES_FROM": "future",
    }
    parsed = dateparser.parse(text, settings=settings)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = tz.localize(parsed)
    return parsed.astimezone(tz)

def to_iso(dt_obj: dt.datetime) -> str:
    return dt_obj.isoformat()

def now_in_tz(tz_name: str) -> dt.datetime:
    tz = ensure_tz(tz_name)
    return dt.datetime.now(tz)


def parse_nl_datetime(text: str, tz_name="America/Los_Angeles", base=None):
    """
    text: natural language like "next Tuesday at 3pm"
    tz_name: user's timezone
    base: reference datetime; if None uses now()
    """
    tz = pytz.timezone(tz_name)
    base = base or dt.datetime.now(tz)

    settings = {
        "RETURN_AS_TIMEZONE_AWARE": True,
        "TIMEZONE": tz_name,
        "PREFER_DATES_FROM": "future",         # prefer future for ambiguous dates
        "RELATIVE_BASE": base,                 # critical for "next Tuesday", "tomorrow", etc.
    }

    parsed = dateparser.parse(text, settings=settings)
    return parsed


TIME_PHRASE_RE = re.compile(
    r"\b(next|this|tomorrow|today|in)\b.*|\b(mon|tue|wed|thu|fri|sat|sun)\w*\b.*",
    re.IGNORECASE
)

def extract_time_phrase(message: str) -> str | None:
    # very naive rule-based extraction; LLM extraction is often better
    m = TIME_PHRASE_RE.search(message)
    return m.group(0).strip() if m else message

def parse_user_date(msg, base=None):
   phrase = extract_time_phrase(msg)
   dt_obj = parse_nl_datetime(phrase, base=base)
   return dt_obj
