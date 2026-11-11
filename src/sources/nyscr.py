"""
nyscr.py — open ads from the New York State Contract Reporter (nyscr.ny.gov).

The NYSCR "Find Bids" page (/Ads/Search) lists every currently open
opportunity — state agencies, authorities, SUNY/CUNY, plus many counties and
cities — server-rendered, with no login. A plain GET with query parameters
returns the results; paging is ?Top=<page size>&Skip=<offset>, and the
sidebar filters are ordinary query params too (e.g. Categories[]=16 for
"Information Technology", Keyword=...).

Each list item carries title, CR# (the ad's stable id), agency, division,
issue date, due date, location, category and ad type.

NOTE: the full ad (Details page, documents, contact) is LOGIN-ONLY, so we
never fetch it. Each opportunity instead links to the public search filtered
by its CR# (?Keyword=<CR#>), which shows exactly that one listing; a vendor
logs in from there to read the full ad.

Config:
  categories: optional list of NYSCR category ids to restrict to (sidebar
              values; 16 = Information Technology). Empty = all open ads.
  keyword:    optional keyword filter (server-side).
  page_size:  default 100 (the site accepts larger than its 50 max option).
"""

from __future__ import annotations

from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

from .base import Source
from ..models import Opportunity
from ..util import to_iso_date, clean_text

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

_BASE = "https://www.nyscr.ny.gov"
_SEARCH = _BASE + "/Ads/Search"
_MAX_PAGES = 40  # safety cap


class NyscrSource(Source):
    """Reads open ads from the NYS Contract Reporter public search."""

    def __init__(self, cfg: dict):
        self.name = cfg.get("name", "NYS Contract Reporter")
        self.timeout = int(cfg.get("timeout", 60))
        self.categories = [str(c) for c in cfg.get("categories", [])]
        self.keyword = cfg.get("keyword", "")
        self.page_size = int(cfg.get("page_size", 100))

    def fetch(self) -> list[Opportunity]:
        out: dict[str, Opportunity] = {}
        skip = 0
        for _ in range(_MAX_PAGES):
            params = [("Top", self.page_size), ("Skip", skip), ("Sort", "-DateIssued")]
            if self.keyword:
                params.append(("Keyword", self.keyword))
            params += [("Categories[]", c) for c in self.categories]
            resp = requests.get(_SEARCH, params=params, headers={"User-Agent": _BROWSER_UA},
                                timeout=self.timeout)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            if skip == 0 and not soup.select_one("form[action='/Ads/Search']"):
                raise RuntimeError(f"NYSCR search page layout not recognized at {resp.url}")
            items = soup.select("div.opp-list-item[data-ad-id]")
            for item in items:
                opp = self._item_to_opp(item)
                if opp:
                    out[opp.id] = opp
            if len(items) < self.page_size:
                break
            skip += self.page_size
        return list(out.values())

    def _item_to_opp(self, item):
        ad_id = item.get("data-ad-id", "").strip()
        title_div = item.find("div", title=lambda t: t and t.startswith("Full Title:"))
        title = clean_text(title_div["title"][len("Full Title:"):]) if title_div else ""
        if not ad_id or not title:
            return None

        # Every field is a <div class="d-flex"> holding a "Label:" div and a
        # value div. Collect them into a dict keyed by the label.
        fields: dict[str, str] = {}
        for row in item.select("div.d-flex"):
            kids = row.find_all("div", recursive=False)
            if len(kids) < 2:
                continue
            label = clean_text(kids[0].get_text())
            if not label.endswith(":") or label == "Title:":
                continue
            fields.setdefault(label[:-1], clean_text(kids[1].get_text()))

        cr = fields.get("CR#", ad_id)
        agency = fields.get("Agency", "")
        division = fields.get("Division", "")
        desc_parts = [title]
        for key in ("Note", "Division", "Location", "Category", "Ad type"):
            if fields.get(key):
                desc_parts.append(f"{key}: {fields[key]}")

        return Opportunity(
            id=f"nyscr:{cr}",
            source_name=self.name,
            title=title,
            description=" — ".join(desc_parts),
            agency=f"{agency} — {division}" if agency and division else (agency or division),
            state="NY",
            url=f"{_SEARCH}?{urlencode({'Keyword': cr})}",
            set_aside=fields.get("Ad type", ""),
            posted_date=to_iso_date(fields.get("Issue date", "")),
            due_date=to_iso_date(fields.get("Due date", "")),
        )
