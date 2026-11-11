"""
il_higher_ed.py — published competitive notices from the Illinois Higher Ed
Procurement Bulletin (procure.stateuniv.state.il.us).

The Bulletin is where Illinois' public universities (UIUC, UIC, ISU, NIU,
SIU, EIU, WIU, ...) post solicitations. Its public Advanced Search (no
login) is a server-rendered ColdFusion form. We submit the same form the
page does — competitive notice types (Bid, Construction, P&A RFP, QBS, RFI,
RFP, Real Property Lease), status Published, due date from today to a year
out (the form needs at least one "criterion") — then follow the result
pager (10 per page, `start=N`) within the same session.

Each notice links to the public viewNotice.cfm page.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .base import Source
from ..models import Opportunity
from ..util import to_iso_date, clean_text

BASE_URL = "https://www.procure.stateuniv.state.il.us/"
_COMPETITIVE_TYPES = ["Bid", "Construction", "PA RFP", "QBS", "RFI", "RFP", "RPLease"]
_PAGE_SIZE = 10
_MAX_PAGES = 50

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class ILHigherEdSource(Source):
    """Reads published competitive notices from the Illinois Higher Ed Bulletin."""

    def __init__(self, cfg: dict):
        self.name = cfg.get("name", "Illinois Higher Ed Procurement Bulletin")
        self.state = cfg.get("state", "IL")
        self.url = cfg.get("url", BASE_URL + "search.cfm?mName=findAdvancedSearch")
        self.timeout = int(cfg.get("timeout", 60))

    def fetch(self) -> list[Opportunity]:
        s = requests.Session()
        s.headers["User-Agent"] = _BROWSER_UA
        form_page = s.get(self.url, timeout=self.timeout)
        form_page.raise_for_status()
        tok = re.search(r'name="pubTok" value="([^"]+)"', form_page.text)
        if not tok:
            raise RuntimeError("IL Bulletin search token not found")

        today = date.today()
        # multipart/form-data, like the page's own form (enctype on <form>).
        fields = [("mName", "processAdvancedSearch"), ("pubTok", tok.group(1)), ("searchTerm", "")]
        fields += [("noticeType", t) for t in _COMPETITIVE_TYPES]
        fields += [
            ("noticeSearchStatus", "Published"),
            ("selectedNIGPCodes", ""),
            ("dueDateFrom", today.isoformat()),
            ("dueDateTo", (today + timedelta(days=365)).isoformat()),
            ("submit", "Search"),
        ]
        resp = s.post(BASE_URL + "search.cfm", files=[(k, (None, v)) for k, v in fields], timeout=self.timeout)
        resp.raise_for_status()

        results: list[Opportunity] = []
        seen: set[str] = set()
        for page in range(_MAX_PAGES):
            if page:
                resp = s.get(
                    f"{BASE_URL}search.cfm?mName=processAdvancedSearch&openFacetList=&start={page * _PAGE_SIZE}",
                    timeout=self.timeout,
                )
                resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            if page == 0 and soup.select_one("#error-list li"):
                raise RuntimeError(f"IL Bulletin search rejected: {clean_text(soup.select_one('#error-list').get_text())}")
            cards = soup.select("div.notice-preview")
            new = 0
            for card in cards:
                opp = self._card_to_opp(card)
                if opp and opp.id not in seen:
                    seen.add(opp.id)
                    results.append(opp)
                    new += 1
            if len(cards) < _PAGE_SIZE or not new:
                break
        return results

    def _card_to_opp(self, card):
        link = card.select_one("h3 a")
        if not link:
            return None
        title = clean_text(link.get_text())
        num_el = card.select_one(".procurement-number")
        number = clean_text(num_el.get_text()).lstrip("#") if num_el else ""
        inst_el = card.select_one(".instution, .institution")
        inst = clean_text(inst_el.get_text()) if inst_el else ""
        status_el = card.select_one(".notice-status")
        if status_el and "published" not in status_el.get_text().lower():
            return None
        summary_ps = card.select(".notice-summary > p")
        summary = clean_text(summary_ps[-1].get_text(" ")) if len(summary_ps) > 1 else ""

        meta = {}
        for p in card.select(".notice-meta p"):
            strong = p.find("strong")
            if strong:
                key = clean_text(strong.get_text()).rstrip(":").lower()
                meta[key] = clean_text(p.get_text()).split(":", 1)[-1].strip()

        href = link.get("href", "")
        m = re.search(r"[?&]i=([^&]+)&p=([^&]+)", href)
        key = f"{m.group(1)}:{m.group(2)}" if m else (number or title)
        return Opportunity(
            id=f"ilhighered:{key}",
            source_name=self.name,
            title=f"{number} — {title}" if number else title,
            description=summary or title,
            agency=f"{inst} (Illinois public higher ed)" if inst else "Illinois public higher education",
            state=self.state,
            url=urljoin(BASE_URL, href),
            posted_date=to_iso_date(meta.get("published")),
            due_date=to_iso_date(meta.get("due date")),
        )
