"""
oracle_fusion.py — open solicitations from Oracle Fusion Cloud Procurement
public "Negotiation Abstracts" pages.

Many governments run Oracle Fusion Cloud Procurement and publish open
solicitations to suppliers on a public page, e.g.
  https://<host>/fscmUI/faces/NegotiationAbstracts?prcBuId=<bu_id>        (classic ADF UI)
  https://<host>/fscmUI/redwood/negotiation-abstracts/view/abstractlisting?prcBuId=<bu_id>  (Redwood UI)

Both UIs are backed by the same Oracle REST resource, which the Redwood page
calls anonymously (no sign-in, no cookie):
  GET https://<host>/fscmRestApi/resources/latest/supplierNegotiationAbstracts
      ?finder=RowFinderByBU;ProcurementBUId=<bu_id>
      &q=CloseDate>'<now>'&onlyData=true&limit=..&offset=..
It returns plain JSON (Negotiation #, title, synopsis, type, posting/close
dates, status, buyer). A finder is required. The server caps a single query at
~500 rows, so we filter server-side to solicitations whose close date is still
in the future (otherwise years of closed/awarded history crowd out the open
ones), then also drop CLOSED/AWARDED/CANCELLED rows client-side.

Config-driven: a list of agencies, each {base_url, bu_id, agency, state}.
`base_url` is the scheme+host from the agency's abstracts URL and `bu_id` is
its `prcBuId` query parameter.

NOTE: there's no anonymous per-solicitation detail URL that deep-links
reliably across both UIs, so every opportunity links to the agency's public
abstracts listing page — find the specific item there by its Negotiation #
(kept in `set_aside`, matching the other adapters' convention).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

from .base import Source
from ..models import Opportunity
from ..util import to_iso_date, clean_text

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

_RESOURCE = "/fscmRestApi/resources/latest/supplierNegotiationAbstracts"
_PAGE_SIZE = 100
_MAX_PAGES = 20  # safety stop
_DONE_STATUSES = {"CLOSED", "AWARDED", "CANCELLED", "COMPLETED"}
# Amended solicitations carry the amendment number after a comma
# ("SOL000017,3"); strip it so the id stays stable across amendments.
_AMENDMENT_SUFFIX = re.compile(r",\s*\d+$")


class OracleFusionSource(Source):
    """Reads open solicitations from Oracle Fusion public negotiation abstracts."""

    def __init__(self, cfg: dict):
        self.name = cfg.get("name", "Oracle Fusion")
        self.agencies = cfg.get("agencies", [])
        self.timeout = int(cfg.get("timeout", 45))

    def fetch(self) -> list[Opportunity]:
        return self.fetch_each(self.agencies, self._fetch_agency)

    def _fetch_agency(self, ag: dict) -> list[Opportunity]:
        base = ag["base_url"].rstrip("/")
        if not base.startswith("http"):
            base = "https://" + base
        host = urlparse(base).netloc
        bu_id = str(ag["bu_id"])
        agency_name = ag.get("agency", host)
        state = ag.get("state", "")
        listing_url = f"{base}/fscmUI/faces/NegotiationAbstracts?prcBuId={bu_id}"
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")

        items: list[dict] = []
        offset = 0
        for _ in range(_MAX_PAGES):
            resp = requests.get(
                base + _RESOURCE,
                params={
                    "finder": f"RowFinderByBU;ProcurementBUId={bu_id}",
                    "q": f"CloseDate>'{now}'",
                    "onlyData": "true",
                    "limit": _PAGE_SIZE,
                    "offset": offset,
                },
                headers={"User-Agent": _BROWSER_UA, "Accept": "application/json"},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            page = data.get("items", [])
            items.extend(page)
            if not data.get("hasMore") or not page:
                break
            offset += len(page)

        results: list[Opportunity] = []
        seen: set[str] = set()
        for it in items:
            if str(it.get("NegotiationStatusCode") or "").upper() in _DONE_STATUSES:
                continue
            opp = self._item_to_opp(it, host, listing_url, agency_name, state)
            if opp and opp.id not in seen:
                seen.add(opp.id)
                results.append(opp)
        return results

    def _item_to_opp(self, it: dict, host: str, listing_url: str, agency_name: str, state: str):
        title = clean_text(it.get("NegotiationTitle"))
        if not title:
            return None
        neg = clean_text(it.get("Negotiation"))
        key = _AMENDMENT_SUFFIX.sub("", neg) or str(it.get("AuctionHeaderId") or title)
        synopsis = clean_text(it.get("Synopsis"))
        neg_type = clean_text(it.get("NegotiationType"))
        desc = synopsis or title
        if neg_type:
            desc = f"[{neg_type}] {desc}"

        return Opportunity(
            id=f"oraclefusion:{host}:{key}",
            source_name=f"{self.name} — {agency_name}",
            title=title,
            description=desc,
            agency=agency_name,
            state=state,
            url=listing_url,
            set_aside=neg,
            posted_date=to_iso_date(it.get("PostingDate") or it.get("PublishDate")),
            due_date=to_iso_date(it.get("CloseDate")),
        )
