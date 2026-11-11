"""
bidlocker.py — open solicitations from Bid Locker (bidlocker.us) agencies.

Bid Locker is a multi-agency e-bidding platform (mostly OR, MD, KY, WA, PA
public agencies). Every agency has a public page at /a/<slug> and — what we
use — a public per-agency RSS feed at /a/<slug>/rss listing its open
solicitations: title, detail link, short description, and <category> tags
"Status; open", "ProjectNo; ...", "CloseDate; Nov 30, 2026 4:00PM". No
login, plain GET.

(There is also a nationwide feed /r/_/rss, but it is capped at 40 items and
has no agency name, so we go per agency.)

Config-driven: give it a list of agencies (slug + optional name + state).
The slug is the path segment in the agency's URL, e.g.
bidlocker.us/a/psu_or -> "psu_or". Or set `discover: true` to read the
current agency list from the nationwide page (bidlocker.us/r/_) on every run
— state can't be inferred then, so it is left blank unless listed.

Each opportunity links to the public bid detail page (bidlocker.us/details/<id>);
the numeric id is the stable key.
"""

from __future__ import annotations

import html as _html
import re
import xml.etree.ElementTree as ET

import requests

from .base import Source
from ..models import Opportunity
from ..util import to_iso_date, clean_text

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

_BASE = "https://bidlocker.us"
_DETAIL_ID = re.compile(r"/details/(\d+)")
_SLUG_LINK = re.compile(r'href="/a/([A-Za-z0-9_\-]+)"')
_TITLE_SUFFIX = re.compile(r"\s*Contracting Opportunities\s*$", re.I)


def _parse_rss(content: bytes):
    """Bid Locker feeds declare encoding="utf-16" but per-agency feeds are
    actually served as UTF-8 (the nationwide one really is UTF-16). Decode by
    sniffing, drop the XML declaration, and parse the text."""
    if content[:2] in (b"\xff\xfe", b"\xfe\xff") or b"\x00" in content[:20]:
        text = content.decode("utf-16")
    else:
        text = content.decode("utf-8-sig", errors="replace")
    text = re.sub(r"^\s*<\?xml[^>]*\?>", "", text)
    return ET.fromstring(text)


class BidLockerSource(Source):
    """Reads open solicitations from a list of Bid Locker agency RSS feeds."""

    def __init__(self, cfg: dict):
        self.name = cfg.get("name", "Bid Locker")
        self.agencies = cfg.get("agencies", [])
        self.discover = bool(cfg.get("discover", False))
        self.timeout = int(cfg.get("timeout", 30))

    def _get(self, path: str) -> requests.Response:
        resp = requests.get(_BASE + path, headers={"User-Agent": _BROWSER_UA}, timeout=self.timeout)
        resp.raise_for_status()
        return resp

    def fetch(self) -> list[Opportunity]:
        agencies = [a for a in self.agencies if a.get("enabled", True)]
        if self.discover:
            listed = {a["slug"] for a in self.agencies}
            html = self._get("/r/_").text
            for slug in sorted(set(_SLUG_LINK.findall(html))):
                if slug not in listed:
                    agencies.append({"slug": slug})
            if not agencies:
                raise RuntimeError("Bid Locker: no agencies discovered on /r/_ (layout change?)")

        out = {opp.id: opp for opp in self.fetch_each(agencies, self._fetch_agency)}
        return list(out.values())

    def _fetch_agency(self, ag: dict) -> list[Opportunity]:
        slug = ag["slug"]
        root = _parse_rss(self._get(f"/a/{slug}/rss").content)
        channel = root.find("channel")
        if channel is None:
            raise RuntimeError(f"Bid Locker feed for {slug!r} has no <channel>")
        agency_name = ag.get("agency") or _TITLE_SUFFIX.sub("", clean_text(channel.findtext("title"))) or slug
        state = ag.get("state", "")

        results: list[Opportunity] = []
        for item in channel.findall("item"):
            opp = self._item_to_opp(item, slug, agency_name, state)
            if opp:
                results.append(opp)
        return results

    def _item_to_opp(self, item, slug: str, agency_name: str, state: str):
        title = clean_text(item.findtext("title"))
        link = clean_text(item.findtext("link"))
        guid = clean_text(item.findtext("guid"))
        m = _DETAIL_ID.search(guid) or _DETAIL_ID.search(link)
        if not title or not m:
            return None
        cats: dict[str, str] = {}
        for c in item.findall("category"):
            key, _, val = (c.text or "").partition(";")
            cats[key.strip().lower()] = val.strip()
        if cats.get("status", "open").lower() != "open":
            return None
        project_no = cats.get("projectno", "")
        desc = clean_text(item.findtext("description"))
        # Descriptions arrive double-escaped (&amp;ldquo;) — one more unescape.
        desc = clean_text(_html.unescape(desc))
        return Opportunity(
            id=f"bidlocker:{m.group(1)}",
            source_name=f"{self.name} — {agency_name}",
            title=title,
            description=" — ".join(x for x in (title, desc) if x),
            agency=agency_name,
            state=state,
            url=link or f"{_BASE}/details/{m.group(1)}",
            set_aside=project_no,
            posted_date=to_iso_date(item.findtext("pubDate")),
            due_date=to_iso_date(cats.get("closedate", "")),
        )
