"""
atlanta_regional.py — open solicitations from the Atlanta Regional Commission
(ARC), GA.

A one-off adapter: procurement.atlantaregional.org is a WordPress site whose
"Bid Opportunities" are a custom post type, `bid-opportunity`, exposed through
the standard public WordPress REST API (no login; browsing is public, only
submitting a proposal needs an account):

    /wp-json/wp/v2/bid-opportunity?per_page=100&page=N

Each post carries the title (e.g. "RFP828 – Interactive Web Mapping ..."),
the permalink to its detail page, the full description in content.rendered,
the posted date, and ACF fields including proposalDate (the due date). The
open/closed/cancelled/awarded status is a taxonomy term surfaced in
class_list as "opportunity-status-<status>", so we keep only
"opportunity-status-open". Paging follows the X-WP-TotalPages header.

(The HTML listing at /opportunities/status/open/ shows the same records as
cards, but the JSON is cleaner and carries the full description.)
"""

from __future__ import annotations

import html
import re

import requests

from .base import Source
from ..models import Opportunity
from ..util import to_iso_date, clean_text

LISTING_URL = "https://procurement.atlantaregional.org/"
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
# Solicitation number at the start of the title, e.g. "RFP828", "RFP804(v2)", "NOFA800".
_SOL_NO = re.compile(r"^\s*([A-Z]{2,6}\d+(?:\([^)]*\))?)")


class AtlantaRegionalSource(Source):
    """Reads open bid opportunities from ARC's WordPress procurement site."""

    def __init__(self, cfg: dict):
        self.name = cfg.get("name", "Atlanta Regional Commission, GA")
        self.state = cfg.get("state", "GA")
        self.url = cfg.get("url", LISTING_URL)
        self.timeout = int(cfg.get("timeout", 30))

    def fetch(self) -> list[Opportunity]:
        api = self.url.rstrip("/") + "/wp-json/wp/v2/bid-opportunity"
        results: list[Opportunity] = []
        page, total_pages = 1, 1
        while page <= total_pages:
            resp = requests.get(api, params={"per_page": 100, "page": page},
                                headers={"User-Agent": _BROWSER_UA}, timeout=self.timeout)
            resp.raise_for_status()
            total_pages = int(resp.headers.get("X-WP-TotalPages", page) or page)
            posts = resp.json()
            if not isinstance(posts, list):
                raise RuntimeError(f"ARC: unexpected REST response: {str(posts)[:200]}")
            for post in posts:
                opp = self._post_to_opp(post)
                if opp:
                    results.append(opp)
            page += 1
        return results

    def _post_to_opp(self, post: dict):
        classes = post.get("class_list") or []
        if isinstance(classes, dict):
            classes = list(classes.values())
        if "opportunity-status-open" not in classes:
            return None

        title = clean_text(html.unescape((post.get("title") or {}).get("rendered", "")))
        if not title:
            return None
        m = _SOL_NO.match(title)
        sol_no = m.group(1) if m else str(post.get("id", ""))
        acf = post.get("acf") or {}
        if not isinstance(acf, dict):
            acf = {}
        description = clean_text((post.get("content") or {}).get("rendered", "")) or title
        # Drop the boilerplate registration notice the site prefixes to every post.
        description = re.sub(r"^(\*\*[^*]+\*\*\s*)+", "", description).strip() or description

        return Opportunity(
            id=f"arc:{sol_no}",
            source_name=self.name,
            title=title,
            description=description,
            agency="Atlanta Regional Commission",
            state=self.state,
            url=post.get("link") or self.url,
            posted_date=to_iso_date(post.get("date")),
            due_date=to_iso_date(acf.get("proposalDate")),
        )
