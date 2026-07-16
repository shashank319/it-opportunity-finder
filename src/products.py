"""
products.py — turns the day's opportunities into "product opportunities".

Idea: one RFP is a single client's need. But when MANY different governments ask
for the same kind of software, that's a validated, recurring problem you could
build once and sell to many — a product. This module groups the kept
opportunities into software themes (from config) and ranks each theme by how
many DISTINCT agencies and states are asking for it.

The result feeds the "Product Opportunities" tab on the dashboard.
"""

from __future__ import annotations

from .models import Opportunity
from .pipeline import _compile_terms   # reuse the whole-word keyword matcher


def build_product_themes(opps: list[Opportunity], config: dict) -> list[dict]:
    cfg = config.get("product_themes", {}) or {}
    min_agencies = int(cfg.get("min_agencies", 2))
    themes_cfg = cfg.get("themes", []) or []

    results = []
    for theme in themes_cfg:
        name = theme.get("name", "Untitled theme")
        matchers = _compile_terms(theme.get("keywords", []))

        # Match on the TITLE (the project's actual subject). Matching the full
        # description let vague/bundled projects match many unrelated themes, so
        # title-only keeps each theme clean and meaningful.
        matched: list[Opportunity] = []
        for o in opps:
            title = o.title.lower()
            if any(rx.search(title) for _, rx in matchers):
                matched.append(o)

        if not matched:
            continue

        agencies = sorted({o.agency for o in matched if o.agency})
        states = sorted({o.state for o in matched if o.state})
        if len(agencies) < min_agencies:
            continue

        # Best examples first (highest IT relevance).
        examples = sorted(matched, key=lambda o: -o.it_score)[:10]
        results.append({
            "name": name,
            "opportunity_count": len(matched),
            "agency_count": len(agencies),
            "state_count": len(states),
            "states": states,
            # Demand = distinct agencies matter most, then geographic spread.
            "demand_score": len(agencies) * 3 + len(states),
            "examples": [{
                "title": o.title,
                "agency": o.agency,
                "state": o.state,
                "url": o.url,
                "due_date": o.due_date,
                "it_score": o.it_score,
                "source_name": o.source_name,
            } for o in examples],
        })

    # Rank: most agencies first, then most states, then most opportunities.
    results.sort(key=lambda r: (-r["agency_count"], -r["state_count"], -r["opportunity_count"]))
    return results
