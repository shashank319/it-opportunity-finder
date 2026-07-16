"""
arcgis.py — one generic adapter for ArcGIS "feature layer" query APIs.

Some governments publish their open solicitations as an ArcGIS feature layer,
which exposes a public query endpoint returning JSON:
    <layer>/query?where=...&outFields=*&f=json

Example (verified live): Washington, D.C. "Solicitations from PASS".

Config-driven like the Socrata adapter: give it the layer's query URL, an
optional `where` filter, and a field map. ArcGIS date fields are usually epoch
milliseconds, which we convert to plain dates.
"""

from __future__ import annotations

from datetime import datetime, timezone

import requests

from .base import Source
from ..models import Opportunity
from ..util import to_iso_date, clean_text


def _arcgis_date(value) -> str:
    """ArcGIS dates are epoch milliseconds. Convert to 'YYYY-MM-DD'."""
    if value in (None, "", 0):
        return ""
    try:
        # Epoch milliseconds -> date (UTC).
        ms = int(value)
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date().isoformat()
    except (ValueError, TypeError, OSError):
        # Not epoch ms? Fall back to the generic date parser.
        return to_iso_date(value)


class ArcGisSource(Source):
    """Reads opportunities from an ArcGIS feature-layer query endpoint."""

    def __init__(self, cfg: dict):
        self.name = cfg.get("name") or "ArcGIS layer"
        self.query_url = cfg["query_url"].rstrip("/")
        self.state = cfg.get("state", "")
        self.where = cfg.get("where", "1=1")
        self.field_map = cfg.get("field_map", {})
        # Fields that should be treated as epoch-ms dates.
        self.date_fields = set(cfg.get("date_fields", ["posted_date", "due_date"]))
        self.limit = int(cfg.get("limit", 500))
        self.url_template = cfg.get("url_template", "")
        self.source_url = cfg.get("source_url", "")
        self.timeout = int(cfg.get("timeout", 45))

    def fetch(self) -> list[Opportunity]:
        params = {
            "where": self.where,
            "outFields": "*",
            "f": "json",
            "resultRecordCount": self.limit,
            "orderByFields": self.field_map.get("due_date", ""),
        }
        # Drop empty orderByFields so we don't send a blank sort.
        if not params["orderByFields"]:
            params.pop("orderByFields")

        resp = requests.get(self.query_url + "/query", params=params, timeout=self.timeout)
        resp.raise_for_status()
        features = resp.json().get("features", []) or []

        out: list[Opportunity] = []
        for feat in features:
            attrs = feat.get("attributes", {}) or {}
            opp = self._to_opp(attrs)
            if opp:
                out.append(opp)
        return out

    def _get(self, attrs: dict, our_field: str) -> str:
        col = self.field_map.get(our_field)
        if not col:
            return ""
        val = attrs.get(col, "")
        if our_field in self.date_fields:
            return _arcgis_date(val)
        return clean_text(val)

    def _to_opp(self, attrs: dict):
        title = self._get(attrs, "title")
        if not title:
            return None
        raw_id = self._get(attrs, "id") or title
        url = self._get(attrs, "url")
        if not url and self.url_template:
            try:
                url = self.url_template.format(**attrs)
            except Exception:
                url = self.source_url
        return Opportunity(
            id=f"arcgis:{raw_id}",
            source_name=self.name,
            title=title,
            description=self._get(attrs, "description") or title,
            agency=self._get(attrs, "agency"),
            state=self.state,
            url=url or self.source_url,
            posted_date=self._get(attrs, "posted_date"),
            due_date=self._get(attrs, "due_date"),
        )
