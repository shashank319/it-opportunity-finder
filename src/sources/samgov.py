"""
samgov.py — FEDERAL opportunities from SAM.gov (the U.S. government's official
contract-opportunity system).

NOTE ON SCOPE: the rest of this tool is state/county/city (SLED). SAM.gov is the
one FEDERAL source, added deliberately and kept as its own on/off adapter. Every
record it returns is tagged state = "US-FED" so you can filter federal in or out
on the dashboard with one click.

FREE API: SAM.gov offers a free "Get Opportunities" API. You need a free API key
(from your SAM.gov account) stored in the GitHub secret SAM_API_KEY. Without the
key this source logs a clear error and is skipped — the rest of the run continues.

Rate limits are modest (roughly 10 requests/day without a registered entity,
higher with one). We make ONE request per configured NAICS code per run, so keep
the NAICS list short (the IT ones below are plenty).
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

import requests

from .base import Source
from ..models import Opportunity
from ..util import to_iso_date, clean_text

SAM_SEARCH_URL = "https://api.sam.gov/opportunities/v2/search"


class SamGovSource(Source):
    """Federal contract opportunities from SAM.gov (config-driven)."""

    def __init__(self, cfg: dict):
        self.name = cfg.get("name", "SAM.gov (Federal)")
        self.lookback_days = int(cfg.get("lookback_days", 3))
        # One API request per NAICS code (keeps volume + rate-limit use low).
        self.naics_codes = [str(c) for c in cfg.get("naics_codes", [])]
        # Notice types: p=presolicitation, o=solicitation, k=combined synopsis,
        # r=sources sought. Awards ('a') are intentionally excluded (already won).
        self.notice_types = cfg.get("notice_types", ["p", "o", "k", "r"])
        self.limit = int(cfg.get("limit", 1000))
        self.timeout = int(cfg.get("timeout", 60))

    def fetch(self) -> list[Opportunity]:
        api_key = os.environ.get("SAM_API_KEY")
        if not api_key:
            # Raise a clear message; the runner logs it and skips this source.
            raise RuntimeError(
                "SAM_API_KEY secret is not set. Get a free key from your SAM.gov "
                "account and add it as a GitHub Actions secret (see README)."
            )

        today = datetime.now(timezone.utc)
        posted_from = (today - timedelta(days=self.lookback_days)).strftime("%m/%d/%Y")
        posted_to = today.strftime("%m/%d/%Y")

        seen: dict[str, Opportunity] = {}
        # If no NAICS configured, do a single broad pull (date + notice type only).
        naics_list = self.naics_codes or [None]

        for naics in naics_list:
            params = {
                "api_key": api_key,
                "postedFrom": posted_from,
                "postedTo": posted_to,
                "limit": self.limit,
                "offset": 0,
                "ptype": ",".join(self.notice_types),
            }
            if naics:
                params["ncode"] = naics

            resp = requests.get(SAM_SEARCH_URL, params=params, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()

            for rec in data.get("opportunitiesData", []) or []:
                opp = self._to_opportunity(rec)
                if opp and opp.id not in seen:
                    seen[opp.id] = opp

            time.sleep(0.5)  # be polite; stay well under the rate limit

        return list(seen.values())

    # -- helpers -----------------------------------------------------------

    def _to_opportunity(self, rec: dict):
        title = clean_text(rec.get("title"))
        if not title:
            return None
        notice_id = rec.get("noticeId") or rec.get("solicitationNumber") or title
        set_aside = rec.get("typeOfSetAsideDescription") or rec.get("typeOfSetAside") or ""
        return Opportunity(
            id=f"samgov:{notice_id}",
            source_name=self.name,
            title=title,
            # SAM's "description" field is a URL to fetch full text (needs another
            # keyed call); the title is the reliable text for filtering/display.
            description=title,
            agency=clean_text(rec.get("fullParentPathName") or rec.get("department")),
            state="US-FED",
            url=rec.get("uiLink", ""),
            naics=clean_text(rec.get("naicsCode")),
            psc=clean_text(rec.get("classificationCode")),
            set_aside=clean_text(set_aside),
            posted_date=to_iso_date(rec.get("postedDate")),
            due_date=to_iso_date(rec.get("responseDeadLine")),
        )
