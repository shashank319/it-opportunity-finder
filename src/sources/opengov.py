"""
opengov.py — open projects from OpenGov Procurement agency portals.

OpenGov Procurement (formerly ProcureNow) is used by many cities, counties, and
states. Each agency has a public portal identified by a `slug`, e.g.:
    https://procurement.opengov.com/portal/santa-monica-ca

The main portal is behind a JavaScript/Cloudflare wall, BUT the "embed" view is
publicly fetchable with a plain request and server-renders the project data into
the page as a big JavaScript object (`window.__data = {...}`):
    https://procurement.opengov.com/portal/embed/<slug>/project-list

That object isn't strict JSON (it contains JS functions), so we can't parse the
whole thing. Instead we scan it for the individual PROJECT objects — those ARE
clean JSON — and keep the ones whose status is "open".

Config-driven: add an agency by adding a {slug, agency, state} entry. Find a
slug from the agency's OpenGov portal URL.

NOTE: this parses an embedded page structure, so it's more fragile than a plain
API. If OpenGov changes the page, this source just logs an error and is skipped.
"""

from __future__ import annotations

import json
import requests

from .base import Source
from ..models import Opportunity
from ..util import to_iso_date, clean_text

EMBED_URL = "https://procurement.opengov.com/portal/embed/{slug}/project-list"
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


def extract_open_projects(html: str) -> list[dict]:
    """Pull project objects out of the embedded page.

    We walk the HTML tracking string/brace state, and whenever a balanced
    {...} block both contains "proposalDeadline" and parses as JSON, we treat it
    as a project. (Larger wrapper objects contain JS functions and fail to parse,
    so this naturally isolates the clean project objects.)
    """
    needle = '"proposalDeadline"'
    out: list[dict] = []
    stack: list[int] = []
    i, n = 0, len(html)
    in_str = esc = False
    while i < n:
        c = html[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                stack.append(i)
            elif c == "}":
                if stack:
                    start = stack.pop()
                    frag = html[start:i + 1]
                    if needle in frag and len(frag) < 40000:
                        try:
                            obj = json.loads(frag)
                        except Exception:
                            obj = None
                        if isinstance(obj, dict) and obj.get("title"):
                            out.append(obj)
        i += 1

    # Keep the smallest object per project id (avoids nested duplicates).
    smallest: dict = {}
    for o in out:
        key = o.get("id") or o.get("financialId")
        if key is None:
            continue
        if key not in smallest or len(json.dumps(o)) < len(json.dumps(smallest[key])):
            smallest[key] = o
    return list(smallest.values())


class OpenGovSource(Source):
    """Reads OPEN projects from a list of OpenGov Procurement portals."""

    def __init__(self, cfg: dict):
        self.name = cfg.get("name", "OpenGov")
        self.agencies = cfg.get("agencies", [])
        self.max_pages = int(cfg.get("max_pages", 3))
        self.timeout = int(cfg.get("timeout", 30))

    def fetch(self) -> list[Opportunity]:
        out: list[Opportunity] = []
        for ag in self.agencies:
            if not ag.get("enabled", True):
                continue
            out.extend(self._fetch_agency(ag))
        return out

    def _fetch_agency(self, ag: dict) -> list[Opportunity]:
        slug = ag["slug"]
        agency_name = ag.get("agency", slug)
        state = ag.get("state", "")
        headers = {"User-Agent": _BROWSER_UA, "Accept": "text/html"}

        results: list[Opportunity] = []
        seen_ids: set = set()
        for page in range(1, self.max_pages + 1):
            url = EMBED_URL.format(slug=slug)
            resp = requests.get(url, params={"page": page}, headers=headers, timeout=self.timeout)
            resp.raise_for_status()
            projects = extract_open_projects(resp.text)
            new_this_page = 0
            for p in projects:
                if str(p.get("status", "")).lower() != "open":
                    continue
                pid = p.get("id") or p.get("financialId")
                if pid in seen_ids:
                    continue
                seen_ids.add(pid)
                new_this_page += 1
                results.append(self._project_to_opp(p, slug, agency_name, state))
            # No new projects on this page => stop paginating.
            if new_this_page == 0:
                break
        return results

    def _project_to_opp(self, p: dict, slug: str, agency_name: str, state: str) -> Opportunity:
        org = (p.get("government") or {}).get("organization", {}) or {}
        pid = p.get("id") or p.get("financialId")
        return Opportunity(
            id=f"opengov:{slug}:{pid}",
            source_name=f"{self.name} — {agency_name}",
            title=clean_text(p.get("title")),
            description=clean_text(p.get("summary") or p.get("title")),
            agency=clean_text(org.get("name") or agency_name),
            state=state,
            url=f"https://procurement.opengov.com/portal/{slug}/projects/{pid}",
            posted_date=to_iso_date(p.get("created_at")),
            due_date=to_iso_date(p.get("proposalDeadline")),
            set_aside=clean_text(p.get("financialId") or ""),
        )
