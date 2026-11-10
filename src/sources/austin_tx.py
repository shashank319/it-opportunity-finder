"""
austin_tx.py — open solicitations from the City of Austin, TX (Austin Finance Online).

financeonline.austintexas.gov/afo/account_services/solicitation/solicitations.cfm
is a public (no login), server-rendered ColdFusion page listing every
currently open City solicitation as a "well" card: solicitation number
(linked to solicitation_details.cfm?sid=N), due date, title and summary.

The repo's Austin Socrata dataset is historical *awarded* contracts; this is
the live open-bid list.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .base import Source
from ..models import Opportunity
from ..util import to_iso_date, clean_text

LISTING_URL = (
    "https://financeonline.austintexas.gov/afo/account_services/solicitation/solicitations.cfm"
)
_SID = re.compile(r"sid=(\d+)")
_DUE = re.compile(r"Due Date:\s*(\d{1,2}/\d{1,2}/\d{4})")

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class AustinTXSource(Source):
    """Reads open solicitations from the City of Austin TX."""

    def __init__(self, cfg: dict):
        self.name = cfg.get("name", "City of Austin, TX")
        self.state = cfg.get("state", "TX")
        self.url = cfg.get("url", LISTING_URL)
        self.timeout = int(cfg.get("timeout", 60))

    def fetch(self) -> list[Opportunity]:
        resp = requests.get(self.url, headers={"User-Agent": _BROWSER_UA}, timeout=self.timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        results: list[Opportunity] = []
        seen: set[str] = set()
        for card in soup.select("div.well.parent div.child"):
            opp = self._card_to_opp(card)
            if opp and opp.id not in seen:
                seen.add(opp.id)
                results.append(opp)
        if not results and "solicitation_details.cfm" in resp.text:
            raise RuntimeError("Austin solicitation cards present but none parsed (layout changed?)")
        return results

    def _card_to_opp(self, card):
        link = card.find("a", href=_SID)
        if not link:
            return None
        sid = _SID.search(link["href"]).group(1)
        number = clean_text(link.get_text())
        strongs = [s for s in card.find_all("strong", recursive=False) if not s.find("a")]
        title = clean_text(strongs[0].get_text()) if strongs else ""
        if not title:
            return None
        text = clean_text(card.get_text(" "))
        due = _DUE.search(text)
        # Summary = everything after the title.
        summary = text.split(title, 1)[1].strip() if title in text else ""
        return Opportunity(
            id=f"austintx:{sid}",
            source_name=self.name,
            title=f"{number} — {title}" if number else title,
            description=summary or title,
            agency="City of Austin, TX",
            state=self.state,
            url=urljoin(self.url, link["href"]),
            due_date=to_iso_date(due.group(1)) if due else "",
        )
