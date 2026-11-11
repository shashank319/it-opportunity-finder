"""
tennessee_cpo.py — open solicitations from the State of Tennessee Central
Procurement Office (CPO).

A one-off adapter. The GO-BID "Current Procurement Opportunities" page is
only a hub of links (no table — hence no <tr> in its HTML). The CPO's actual
listings live on two server-rendered tn.gov (Adobe AEM) pages, public, no
login, each with one plain HTML <table>:

  * Invitations to Bid (ITB)
    .../supplier-information/invitations-to-bid--itb-.html
    columns: Event ID + document links | Start <br> Response Due | Event Name | Last Updated
  * Request for Proposals (RFP) Opportunities (also RFIs, RFQs, notices)
    .../supplier-information/request-for-proposals--rfp--opportunities1.html
    columns: Document ID + links | Start <br> Response Due | Title | Last Updated

Both tables are hand-maintained and keep closed events listed for months, so
we keep only rows whose response-due date is today or later (rows without a
parseable due date are kept). Hand-entered years are sometimes wrong (e.g.
start 11/06/2026, due 01/11/2026); when the due date is before the start
date we roll it forward a year so the event isn't dropped as "closed".

Each record links to the first document in its row (the ITB/RFP itself).
Bidding happens in the Edison supplier portal (login), but the listings and
documents are public.
"""

from __future__ import annotations

import re
from datetime import date
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .base import Source
from ..models import Opportunity
from ..util import to_iso_date, clean_text, today_iso

_BASE = "https://www.tn.gov/generalservices/procurement/central-procurement-office--cpo-/supplier-information/"
DEFAULT_PAGES = [
    _BASE + "invitations-to-bid--itb-.html",
    _BASE + "request-for-proposals--rfp--opportunities1.html",
]
LISTING_URL = (
    "https://www.tn.gov/generalservices/procurement/central-procurement-office--cpo-/"
    "go-bid/current-procurement-opportunities.html"
)
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
_DATE = re.compile(r"\d{1,2}/\d{1,2}/\d{4}")
# Solicitation number, e.g. "32701-26-331", "33101-26059FAF7", "529/000-01-2026".
_SOL_NO = re.compile(r"\d[\w/]*(?:-[\w/]+)+")
# Leading document-type word(s), e.g. "RFP", "Event", "Solicitation Notice".
_SOL_TYPE = re.compile(r"^([A-Za-z_ ]+?)\s*\d")


class TennesseeCPOSource(Source):
    """Reads open ITBs and RFPs/RFIs/RFQs from Tennessee CPO's tn.gov pages."""

    def __init__(self, cfg: dict):
        self.name = cfg.get("name", "Tennessee Central Procurement Office")
        self.state = cfg.get("state", "TN")
        # `url` is the public hub page (informational); `pages` are scraped.
        self.url = cfg.get("url", LISTING_URL)
        self.pages = cfg.get("pages", DEFAULT_PAGES)
        self.include_past_due = bool(cfg.get("include_past_due", False))
        self.timeout = int(cfg.get("timeout", 30))

    def fetch(self) -> list[Opportunity]:
        today = today_iso()
        results: list[Opportunity] = []
        seen: set[str] = set()
        for page_url in self.pages:
            resp = requests.get(page_url, headers={"User-Agent": _BROWSER_UA}, timeout=self.timeout)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            table = soup.find("table")
            if table is None:
                raise RuntimeError(f"Tennessee CPO: no listing table found at {page_url}")
            for row in table.find_all("tr"):
                opp = self._row_to_opp(row, page_url)
                if not opp or opp.id in seen:
                    continue
                if not self.include_past_due and opp.due_date and opp.due_date < today:
                    continue
                seen.add(opp.id)
                results.append(opp)
        return results

    def _row_to_opp(self, row, page_url: str):
        cells = row.find_all("td")
        if len(cells) < 3:
            return None  # header row uses <td> too, but has no dates — filtered below
        first_a = cells[0].find("a")
        id_text = clean_text(first_a.get_text()) if first_a else clean_text(cells[0].get_text())
        m = _SOL_NO.search(id_text)
        if not m:
            return None
        sol_no = m.group(0)
        tm = _SOL_TYPE.match(id_text)
        sol_type = clean_text(tm.group(1).replace("_", " ")) if tm else ""
        if sol_type.lower() == "event":
            sol_type = "ITB"

        dates = _DATE.findall(cells[1].get_text(" "))
        if not dates:
            return None
        posted = to_iso_date(dates[0])
        due = to_iso_date(dates[1]) if len(dates) > 1 else ""
        due = _fix_due_year(posted, due)

        title = clean_text(cells[2].get_text())
        if not title:
            return None
        title = re.sub(r"\s*-\s*UPDATED\b.*$", "", title, flags=re.I).strip() or title

        url = urljoin(page_url, first_a.get("href", "")) if first_a and first_a.get("href") else page_url
        return Opportunity(
            id=f"tncpo:{sol_no}",
            source_name=self.name,
            title=f"{sol_type} {sol_no} — {title}".strip(),
            description=title,
            agency="State of Tennessee — Central Procurement Office",
            state=self.state,
            url=url,
            posted_date=posted,
            due_date=due,
        )


def _fix_due_year(posted: str, due: str) -> str:
    """Roll a due date forward a year when a typo puts it before the start date."""
    if not posted or not due or due >= posted:
        return due
    try:
        d = date.fromisoformat(due)
        return d.replace(year=d.year + 1).isoformat()
    except ValueError:
        return due
