"""
nypa.py — open RFQs from the New York Power Authority bid portal
(rfp.nypa.gov).

rfp.nypa.gov is an Angular SPA, but the public "Current Bids" page
(#/rfq-listing) is fed by an unauthenticated JSON API:

    GET https://api.nypa.gov/rfq/v1/api/public/rfq/Bids?statusText=Open
        &userAdminSite=&postDateMinimum=

which returns a plain JSON array (rfqid, rfqNumber, rfqName, postDate,
bidDueDate, facilityName, categoryName, statusText, buyer, ...). No login,
no token — it's the exact call the public page makes. (The SPA's sign-in is
only for vendors subscribing/responding; the listing itself is public.)

Optionally (fetch_details, default on) we also call the public
GetBidDetails?rfqId=<id> endpoint per bid for its "abstract" — the scope
text — which makes keyword matching much better than title-only. It's one
small request per OPEN bid (typically ~20).

Config: `statuses` picks which statusText values to pull (default
["Open"]; "Pre-Bid Advertising" = the "Upcoming Bids" page).
"""

from __future__ import annotations

import requests

from .base import Source
from ..models import Opportunity
from ..util import to_iso_date, clean_text

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

_API = "https://api.nypa.gov/rfq/v1/api/public/rfq/"
_DETAIL_PAGE = "https://rfp.nypa.gov/#/bid-details?rfqId={rfqid}"


class NypaSource(Source):
    """Reads open RFQs from the NY Power Authority public bid API."""

    def __init__(self, cfg: dict):
        self.name = cfg.get("name", "NY Power Authority")
        self.timeout = int(cfg.get("timeout", 30))
        self.statuses = cfg.get("statuses", ["Open"])
        self.fetch_details = bool(cfg.get("fetch_details", True))

    def _get(self, endpoint: str, params: dict):
        resp = requests.get(
            _API + endpoint,
            params=params,
            headers={
                "User-Agent": _BROWSER_UA,
                "Accept": "application/json",
                "Origin": "https://rfp.nypa.gov",
                "Referer": "https://rfp.nypa.gov/",
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def fetch(self) -> list[Opportunity]:
        out: dict[str, Opportunity] = {}
        for status in self.statuses:
            data = self._get("Bids", {"statusText": status, "userAdminSite": "", "postDateMinimum": ""})
            if not isinstance(data, list):
                raise RuntimeError(f"NYPA Bids API returned unexpected payload: {str(data)[:200]}")
            for rec in data:
                opp = self._rec_to_opp(rec)
                if opp:
                    out[opp.id] = opp
        return list(out.values())

    def _rec_to_opp(self, rec: dict):
        rfqid = rec.get("rfqid")
        title = clean_text(rec.get("rfqName"))
        if not rfqid or not title:
            return None
        number = clean_text(rec.get("rfqNumber"))
        facility = clean_text(rec.get("facilityName"))
        category = clean_text(rec.get("categoryName"))

        abstract = ""
        if self.fetch_details:
            try:
                det = self._get("GetBidDetails", {"rfqId": rfqid})
                abstract = clean_text((det or {}).get("abstract"))
            except Exception:
                # A missing abstract shouldn't drop the bid; the listing
                # call above already proved the source itself is healthy.
                abstract = ""

        parts = [f"{number}: {title}" if number else title]
        if category:
            parts.append(f"Category: {category}")
        if facility:
            parts.append(f"Facility: {facility}")
        if abstract:
            parts.append(abstract)

        return Opportunity(
            id=f"nypa:{rfqid}",
            source_name=self.name,
            title=f"{title} ({number})" if number else title,
            description=" — ".join(parts),
            agency="New York Power Authority" + (f" — {facility}" if facility else ""),
            state="NY",
            url=_DETAIL_PAGE.format(rfqid=rfqid),
            set_aside=number,
            posted_date=to_iso_date(rec.get("postDate")),
            due_date=to_iso_date(rec.get("bidDueDate")),
        )
