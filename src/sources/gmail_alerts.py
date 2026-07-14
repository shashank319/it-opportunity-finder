"""
gmail_alerts.py — reads a dedicated Gmail inbox and turns bid-ALERT emails
from shared procurement platforms into normalized Opportunity records.

WHY THIS IS THE PRIMARY LOCAL COVERAGE:
There are tens of thousands of counties/cities. We do NOT scrape each one.
Instead you register (for free) on shared platforms — PlanetBids, Bonfire,
DemandStar, OpenGov, Periscope/BidSync, Public Purchase, Vendor Registry,
BidNet, Ionwave — and set up saved-search / keyword alerts. They email your
dedicated inbox whenever a matching bid is posted across ALL their agencies.
This adapter reads those emails and folds them into the pipeline.

AUTH (read-only, no password stored):
  * You run scripts/gmail_setup.py ONCE on your laptop. It does a Google OAuth
    consent flow (read-only Gmail scope) and prints a token JSON blob.
  * You paste that blob into the GitHub secret GMAIL_TOKEN_JSON.
  * This adapter rebuilds the credential from that blob and refreshes it
    automatically. No Gmail password is ever stored anywhere.

PARSING IS CONFIG-DRIVEN:
Each platform's sender addresses and how to pull links out of its emails are
defined in config.yaml -> sources.gmail_alerts.platforms[]. Add a new alert
provider by adding a config entry — no code change. Because real alert emails
vary, this adapter favors HIGH RECALL: if it can't confidently parse multiple
line-items, it emits one Opportunity per alert email (subject as title) so you
never silently lose a bid.
"""

from __future__ import annotations

import json
import os
import re
import base64

from .base import Source
from ..models import Opportunity
from ..util import clean_text, to_iso_date

# These Google libraries are only needed when the Gmail source is enabled.
# We import lazily inside fetch() so the rest of the tool runs even if they
# aren't installed (e.g. someone only uses Socrata).
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


class GmailAlertsSource(Source):
    """Reads bid-alert emails from a dedicated Gmail inbox."""

    def __init__(self, cfg: dict):
        self.name = cfg.get("name", "Email Alerts (Gmail)")
        self.lookback_days = int(cfg.get("lookback_days", 3))
        self.max_messages = int(cfg.get("max_messages", 200))
        self.platforms = cfg.get("platforms", [])
        # Optional extra Gmail search terms appended to every query (e.g. a label).
        self.extra_query = cfg.get("extra_query", "")

    # -- Google auth / service --------------------------------------------

    def _build_service(self):
        """Rebuild a Gmail API client from the GMAIL_TOKEN_JSON secret."""
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build

        raw = os.environ.get("GMAIL_TOKEN_JSON")
        if not raw:
            raise RuntimeError(
                "GMAIL_TOKEN_JSON secret is not set. Run scripts/gmail_setup.py "
                "to create it (see README)."
            )
        info = json.loads(raw)
        creds = Credentials.from_authorized_user_info(info, GMAIL_SCOPES)
        # Refresh the short-lived access token using the stored refresh token.
        if not creds.valid and creds.refresh_token:
            creds.refresh(Request())
        return build("gmail", "v1", credentials=creds, cache_discovery=False)

    # -- Fetch -------------------------------------------------------------

    def fetch(self) -> list[Opportunity]:
        service = self._build_service()

        opportunities: list[Opportunity] = []
        seen_ids: set[str] = set()

        for platform in self.platforms:
            if not platform.get("enabled", True):
                continue
            query = self._build_query(platform)
            message_ids = self._search(service, query)
            for mid in message_ids:
                msg = self._get_message(service, mid)
                for opp in self._parse_message(msg, platform):
                    if opp.id not in seen_ids:
                        seen_ids.add(opp.id)
                        opportunities.append(opp)
        return opportunities

    # -- Gmail helpers -----------------------------------------------------

    def _build_query(self, platform: dict) -> str:
        """Build a Gmail search string like: newer_than:3d (from:x OR from:y)."""
        froms = platform.get("from_contains", [])
        from_clause = " OR ".join(f"from:{f}" for f in froms) if froms else ""
        parts = [f"newer_than:{self.lookback_days}d"]
        if from_clause:
            parts.append(f"({from_clause})")
        if self.extra_query:
            parts.append(self.extra_query)
        return " ".join(parts)

    def _search(self, service, query: str) -> list[str]:
        result = service.users().messages().list(
            userId="me", q=query, maxResults=self.max_messages
        ).execute()
        return [m["id"] for m in result.get("messages", [])]

    def _get_message(self, service, message_id: str) -> dict:
        return service.users().messages().get(
            userId="me", id=message_id, format="full"
        ).execute()

    # -- Parsing -----------------------------------------------------------

    def _headers(self, msg: dict) -> dict:
        out = {}
        for h in msg.get("payload", {}).get("headers", []):
            out[h.get("name", "").lower()] = h.get("value", "")
        return out

    def _extract_bodies(self, payload: dict) -> tuple[str, str]:
        """Walk the MIME tree and return (plain_text, html)."""
        plain, html = "", ""

        def walk(part):
            nonlocal plain, html
            mime = part.get("mimeType", "")
            body = part.get("body", {})
            data = body.get("data")
            if data:
                decoded = base64.urlsafe_b64decode(data.encode("utf-8")).decode("utf-8", "replace")
                if mime == "text/plain":
                    plain += decoded
                elif mime == "text/html":
                    html += decoded
            for sub in part.get("parts", []) or []:
                walk(sub)

        walk(payload)
        return plain, html

    def _links_from_html(self, html: str) -> list[tuple[str, str]]:
        """Return (href, anchor_text) pairs. Uses BeautifulSoup if available."""
        try:
            from bs4 import BeautifulSoup  # optional dependency
            soup = BeautifulSoup(html, "html.parser")
            out = []
            for a in soup.find_all("a", href=True):
                out.append((a["href"], clean_text(a.get_text())))
            return out
        except Exception:
            # Fallback: crude regex for href="..." with no anchor text.
            return [(m, "") for m in re.findall(r'href="([^"]+)"', html or "")]

    def _parse_message(self, msg: dict, platform: dict) -> list[Opportunity]:
        headers = self._headers(msg)
        subject = clean_text(headers.get("subject", ""))
        sender = clean_text(headers.get("from", ""))
        plain, html = self._extract_bodies(msg.get("payload", {}))
        platform_name = platform.get("name", "Email Alert")

        # Optional per-platform deadline regex applied to the body text.
        due_date = ""
        deadline_regex = platform.get("deadline_regex")
        if deadline_regex:
            m = re.search(deadline_regex, plain + "\n" + html, re.IGNORECASE)
            if m:
                due_date = to_iso_date(m.group(1))

        # Strategy 1 (preferred): one Opportunity per bid-detail link.
        # We only keep links whose URL contains a configured marker, so we don't
        # turn "unsubscribe" / "help" links into fake opportunities.
        link_markers = platform.get("link_contains", [])
        opportunities: list[Opportunity] = []
        if link_markers:
            for href, text in self._links_from_html(html):
                if not any(marker.lower() in href.lower() for marker in link_markers):
                    continue
                title = text or subject
                if not title:
                    continue
                opportunities.append(Opportunity(
                    id=f"gmail:{platform_name}:{self._stable_id(href, title)}",
                    source_name=f"{self.name} — {platform_name}",
                    title=title,
                    description=subject,
                    agency=self._guess_agency(subject, plain, platform),
                    state=platform.get("state", ""),
                    url=href,
                    due_date=due_date,
                ))

        # Strategy 2 (fallback / high recall): if we found no usable links,
        # emit ONE opportunity for the whole email so the bid isn't lost.
        if not opportunities and subject:
            opportunities.append(Opportunity(
                id=f"gmail:{platform_name}:{self._stable_id(msg.get('id',''), subject)}",
                source_name=f"{self.name} — {platform_name}",
                title=subject,
                description=clean_text(plain)[:500] or subject,
                agency=self._guess_agency(subject, plain, platform),
                state=platform.get("state", ""),
                url=self._first_link(html),
                due_date=due_date,
            ))
        return opportunities

    def _guess_agency(self, subject: str, plain: str, platform: dict) -> str:
        """Optional agency extraction via a configured regex; else blank."""
        regex = platform.get("agency_regex")
        if regex:
            m = re.search(regex, subject + "\n" + plain, re.IGNORECASE)
            if m:
                return clean_text(m.group(1))
        return ""

    def _first_link(self, html: str) -> str:
        links = self._links_from_html(html)
        return links[0][0] if links else ""

    @staticmethod
    def _stable_id(*parts: str) -> str:
        """A short, stable id from the given parts (so the same bid dedupes)."""
        import hashlib
        joined = "|".join(p or "" for p in parts)
        return hashlib.sha1(joined.encode("utf-8")).hexdigest()[:16]
