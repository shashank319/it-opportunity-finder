"""
util.py — small shared helpers (date parsing, text cleanup).

Kept tiny and dependency-light so any module can import it.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

try:
    # python-dateutil parses almost any date string; we fall back gracefully
    # if it is not installed.
    from dateutil import parser as _dateparser  # type: ignore
except Exception:  # pragma: no cover
    _dateparser = None


def to_iso_date(value) -> str:
    """Best-effort convert any date-ish value to 'YYYY-MM-DD'. Returns '' on failure.

    Handles Socrata timestamps like '2026-06-23T00:00:00.000', plain dates,
    and common US formats. We only keep the calendar date (deadlines/posted
    dates don't need a time for this tool).
    """
    if not value:
        return ""
    text = str(value).strip()
    if not text:
        return ""

    # Fast path: already looks like an ISO date/datetime.
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    if _dateparser is not None:
        try:
            return _dateparser.parse(text).date().isoformat()
        except Exception:
            return ""
    return ""


def today_iso() -> str:
    """Today's date as 'YYYY-MM-DD' in UTC (GitHub Actions runs in UTC)."""
    return datetime.now(timezone.utc).date().isoformat()


def clean_text(value) -> str:
    """Strip HTML, collapse whitespace, and trim. Safe on None.

    Some open-data feeds (e.g. NYC City Record) store rich-text HTML in their
    description fields. We strip tags so (a) the dashboard shows clean text and
    (b) keyword matching doesn't trip over markup like <span style=...>.
    """
    if value is None:
        return ""
    text = str(value)
    if "<" in text and ">" in text:
        text = re.sub(r"<[^>]+>", " ", text)   # remove HTML tags
        text = _unescape_entities(text)
    return re.sub(r"\s+", " ", text).strip()


def _unescape_entities(text: str) -> str:
    """Turn the few common HTML entities into plain characters."""
    try:
        import html
        return html.unescape(text)
    except Exception:
        return text
