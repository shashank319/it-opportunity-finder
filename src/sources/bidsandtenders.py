"""
bidsandtenders.py — open tenders from Bids&Tenders (bidsandtenders.net) agency
portals.

Unlike the other new sources, Bids&Tenders' listing API is CSRF-protected —
each browser session gets its own anti-forgery token baked into the API URL,
so a plain HTTP request can't replay it directly (it errors out server-side).
We use a headless browser (Playwright) to load the public tenders page like a
real visitor and capture the JSON response the page itself fetches — no
login, no credentials, just observing what the public page already loads.

Config-driven: give it a list of agencies (tenant + name + state). Find the
tenant from the agency's Bids&Tenders URL, e.g. placer.bidsandtenders.net ->
tenant "placer".

Requires Playwright + its Chromium browser (pip install playwright &&
playwright install chromium — see README). If either is missing, this source
raises a clear error and is skipped; every other source keeps running.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from .base import Source
from ..models import Opportunity
from ..util import to_iso_date, clean_text

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def _ms_date(value):
    """Bids&Tenders dates arrive as '/Date(1789671600000)/' (epoch ms).
    Convert to an ISO string util.to_iso_date can pass through as-is."""
    if not isinstance(value, str):
        return ""
    m = re.search(r"/Date\((\d+)\)/", value)
    if not m:
        return ""
    ts = int(m.group(1)) / 1000
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


class BidsAndTendersSource(Source):
    """Reads open tenders from a list of Bids&Tenders agency portals."""

    def __init__(self, cfg: dict):
        self.name = cfg.get("name", "Bids&Tenders")
        self.agencies = cfg.get("agencies", [])
        self.timeout_ms = int(cfg.get("timeout", 30)) * 1000

    def fetch(self) -> list[Opportunity]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise RuntimeError(
                "Bids&Tenders needs Playwright: pip install playwright && "
                "playwright install chromium (see README)."
            )

        out: list[Opportunity] = []
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--disable-blink-features=AutomationControlled"])
            try:
                for ag in self.agencies:
                    if not ag.get("enabled", True):
                        continue
                    out.extend(self._fetch_agency(browser, ag))
            finally:
                browser.close()
        return out

    def _fetch_agency(self, browser, ag: dict) -> list[Opportunity]:
        tenant = ag["tenant"]
        agency_name = ag.get("agency", tenant)
        state = ag.get("state", "")

        captured: dict = {}

        def on_response(resp):
            if "/Tender/Search/" in resp.url and "body" not in captured:
                try:
                    captured["body"] = resp.text()
                except Exception:
                    pass

        context = browser.new_context(
            user_agent=_BROWSER_UA, viewport={"width": 1366, "height": 900}, locale="en-US"
        )
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = context.new_page()
        page.on("response", on_response)
        try:
            page.goto(
                f"https://{tenant}.bidsandtenders.net/Module/Tenders/en",
                wait_until="networkidle", timeout=self.timeout_ms,
            )
            page.wait_for_timeout(1500)
        finally:
            context.close()

        if "body" not in captured:
            raise RuntimeError(f"Bids&Tenders: no tender data captured for tenant '{tenant}'")

        data = json.loads(captured["body"])
        results: list[Opportunity] = []
        for t in data.get("data", []) or []:
            opp = self._tender_to_opp(t, tenant, agency_name, state)
            if opp:
                results.append(opp)
        return results

    def _tender_to_opp(self, t: dict, tenant: str, agency_name: str, state: str):
        if (t.get("Status") or "").lower() != "open":
            return None
        title = clean_text(t.get("Title"))
        if not title:
            return None
        tid = t.get("Id")
        return Opportunity(
            id=f"bidsandtenders:{tenant}:{tid}",
            source_name=f"{self.name} — {agency_name}",
            title=title,
            description=clean_text(t.get("Description")) or title,
            agency=agency_name,
            state=state,
            url=f"https://{tenant}.bidsandtenders.net/Module/Tender/Detail/{tid}",
            posted_date=to_iso_date(_ms_date(t.get("DateAvailable"))),
            due_date=to_iso_date(_ms_date(t.get("DateClosing"))),
        )
