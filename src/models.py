"""
models.py — the single normalized record used everywhere in the tool.

Every source adapter (Socrata, Gmail alerts, future state scrapers) must return
a list of `Opportunity` objects with these fields. Keeping ONE shape means the
pipeline (filter / score / dedupe), the dashboard, and the email digest never
have to care where a record came from.

This file has no external dependencies on purpose — it is safe to import from
anywhere, including tiny helper scripts.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class Opportunity:
    """One government bid / solicitation / RFP, normalized across all sources."""

    # --- Identity ---------------------------------------------------------
    # `id` should be STABLE for the same opportunity across days so dedupe works.
    # Adapters build it from the source's own record id (e.g. solicitation #).
    id: str
    source_name: str            # which adapter/source produced this (e.g. "Montgomery County MD — Solicitations")

    # --- Core content -----------------------------------------------------
    title: str
    description: str = ""
    agency: str = ""            # issuing department / agency
    state: str = ""             # 2-letter state code when known (e.g. "MD"), else ""
    url: str = ""               # link to the original solicitation

    # --- Classification codes (any may be blank depending on the source) --
    naics: str = ""             # federal-style industry code (some local portals include it)
    psc: str = ""               # product/service code
    set_aside: str = ""         # small-business / set-aside flag or text

    # --- Dates (stored as ISO strings "YYYY-MM-DD" when we can parse them) -
    posted_date: str = ""       # when the opportunity was posted
    due_date: str = ""          # response deadline / closing date

    # --- Fields the PIPELINE fills in (adapters leave these at defaults) ---
    it_score: int = 0           # 0-100 relevance score, used only for sorting
    is_new: bool = False        # True if first seen in the most recent run
    first_seen: str = ""        # ISO date this opportunity first appeared in our history
    matched_keywords: list = field(default_factory=list)  # which include-terms hit (handy for debugging)

    def to_dict(self) -> dict:
        """Plain dict for writing to opportunities.json."""
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Opportunity":
        """Rebuild an Opportunity from a dict (e.g. when reading history)."""
        # Only keep keys we know about, so extra/legacy fields don't crash us.
        known = Opportunity.__dataclass_fields__.keys()
        return Opportunity(**{k: v for k, v in d.items() if k in known})
