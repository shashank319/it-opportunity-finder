"""
pipeline.py — filter -> score -> dedupe. Applied uniformly to EVERY source.

Philosophy (from the spec): HIGH RECALL, LIGHT filtering.
  * Keep anything that looks like IT/software/migration/modernization.
  * Drop ONLY clearly non-IT physical work (cabling, paving, HVAC, ...).
  * If an item matches an exclude term BUT also a strong software term, KEEP it
    and let a human decide. Missing a real project is worse than one extra.
  * The 0-100 score is for SORTING ONLY — it never hides anything.

All keyword/code lists and score weights live in config.yaml so a
non-technical teammate can tune them without touching code.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

from .models import Opportunity
from .util import today_iso


def _parse_iso(value: str):
    """Parse a 'YYYY-MM-DD' string to a date, or None if blank/unparseable."""
    if value and len(value) >= 10:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def is_active(opp: Opportunity, today: date, stale_after_days: int) -> bool:
    """True if this looks like a CURRENTLY-OPEN opportunity (accepting bids).

    * Has a deadline  -> keep only if the deadline is today or later.
    * No deadline     -> keep only if it was posted within `stale_after_days`
                         (so we don't show years-old notices with no date).
    * No dates at all -> keep (we can't tell; let a human decide).
    """
    due = _parse_iso(opp.due_date)
    if due is not None:
        return due >= today                      # open if deadline hasn't passed
    posted = _parse_iso(opp.posted_date)
    if posted is not None:
        return posted >= today - timedelta(days=stale_after_days)
    return True


def _compile_terms(terms):
    """Compile keywords into WHOLE-TOKEN regexes.

    Why not a plain substring `in` check? Because "ai" would match "Repair"
    and "app" would match "Apparel" — pure noise. We instead require each
    keyword to sit on token boundaries (start/end or a non-alphanumeric char),
    which still matches whole words like "app", "ai", ".net", "low-code",
    "content management" but not accidental fragments inside other words.

    Returns a list of (original_keyword, compiled_regex).
    """
    compiled = []
    for kw in terms:
        kw = str(kw).lower().strip()
        if not kw:
            continue
        # Boundary = not preceded/followed by a letter or digit. This handles
        # leading/trailing punctuation like ".net" better than \b would.
        pattern = r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])"
        compiled.append((kw, re.compile(pattern)))
    return compiled


class Filters:
    """Pre-compiles the include/exclude lists from config for fast matching."""

    def __init__(self, config: dict):
        f = config.get("filtering", {})
        # Keyword lists become whole-token regexes (see _compile_terms).
        self.include_keywords = _compile_terms(f.get("include_keywords", []))
        self.exclude_keywords = _compile_terms(f.get("exclude_keywords", []))
        # "Strong software" terms: if one of these is present we KEEP the item
        # even when an exclude term also matches.
        self.strong_terms = _compile_terms(f.get("strong_software_keywords", []))
        # Codes that count as IT (NAICS / UNSPSC prefixes / NIGP). Matched as
        # substrings so "43xxxxxx" style prefixes work if you list "43".
        self.include_codes = [str(c).lower() for c in f.get("include_codes", [])]

        # "Active only" date filter (see is_active()).
        self.drop_expired = bool(f.get("drop_expired", True))
        self.stale_after_days = int(f.get("stale_after_days", 150))

        sc = config.get("scoring", {})
        self.tier1_keywords = _compile_terms(sc.get("tier1_keywords", []))
        self.base = int(sc.get("base", 30))
        self.per_hit = int(sc.get("per_hit", 6))
        self.tier1_weight = int(sc.get("tier1_weight", 15))
        self.code_bonus = int(sc.get("code_hit_bonus", 20))


def _text_blob(opp: Opportunity) -> str:
    """Everything we match keywords against, lower-cased."""
    return " ".join([
        opp.title, opp.description, opp.agency,
    ]).lower()


def _codes_blob(opp: Opportunity) -> str:
    return " ".join([opp.naics, opp.psc, opp.set_aside]).lower()


def keep_and_score(opp: Opportunity, filters: Filters) -> tuple[bool, int, list]:
    """Decide whether to KEEP an opportunity and compute its IT score.

    Returns (keep, score, matched_keywords).
    """
    text = _text_blob(opp)
    codes = _codes_blob(opp)

    matched = [kw for kw, rx in filters.include_keywords if rx.search(text)]
    code_hit = any(code and code in codes for code in filters.include_codes)

    # INCLUDE if it matches an IT keyword OR an IT code.
    included = bool(matched) or code_hit
    if not included:
        return False, 0, []

    # EXCLUDE only clearly non-IT physical work — unless a strong software term
    # is also present, in which case we KEEP and let a human decide.
    has_exclude = any(rx.search(text) for _, rx in filters.exclude_keywords)
    has_strong = any(rx.search(text) for _, rx in filters.strong_terms)
    if has_exclude and not has_strong:
        return False, 0, matched

    # --- Score (sorting only) ---
    score = filters.base
    score += filters.per_hit * len(matched)
    tier1_hits = sum(1 for _, rx in filters.tier1_keywords if rx.search(text))
    score += filters.tier1_weight * tier1_hits
    if code_hit:
        score += filters.code_bonus
    score = max(0, min(100, score))

    return True, score, matched


def _dedupe_key(opp: Opportunity) -> str:
    """Primary dedupe key: source + stable id."""
    return f"{opp.source_name}::{opp.id}"


def _cross_source_key(opp: Opportunity) -> str:
    """Secondary key to catch the SAME bid arriving from two sources.

    Normalized title + due date. Deliberately loose; only used to hide obvious
    duplicates, never to drop distinct opportunities.
    """
    title = "".join(ch for ch in opp.title.lower() if ch.isalnum())
    return f"{title}|{opp.due_date}"


def run_pipeline(raw: list[Opportunity], config: dict, history: dict) -> tuple[list, dict]:
    """Filter, score, dedupe, and mark new items.

    Returns (kept_opportunities, updated_history).
    `history` maps dedupe_key -> {"first_seen": "YYYY-MM-DD"} and is updated
    in place with any newly-seen keys.
    """
    filters = Filters(config)
    today = today_iso()
    today_date = datetime.now(timezone.utc).date()

    kept: list[Opportunity] = []
    seen_primary: set[str] = set()
    seen_cross: set[str] = set()

    for opp in raw:
        keep, score, matched = keep_and_score(opp, filters)
        if not keep:
            continue

        # Active-only: drop opportunities whose bidding window has clearly closed.
        if filters.drop_expired and not is_active(opp, today_date, filters.stale_after_days):
            continue

        pkey = _dedupe_key(opp)
        if pkey in seen_primary:
            continue
        ckey = _cross_source_key(opp)
        if ckey in seen_cross:
            continue
        seen_primary.add(pkey)
        seen_cross.add(ckey)

        opp.it_score = score
        opp.matched_keywords = matched

        # New vs. seen-before, using our rolling history file.
        prior = history.get(pkey)
        if prior is None:
            history[pkey] = {"first_seen": today}
            opp.first_seen = today
            opp.is_new = True
        else:
            opp.first_seen = prior.get("first_seen", today)
            opp.is_new = False

        kept.append(opp)

    # Sort: highest IT score first, then soonest deadline (blank deadlines last).
    kept.sort(key=lambda o: (-o.it_score, o.due_date or "9999-99-99"))
    return kept, history
