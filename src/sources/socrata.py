"""
socrata.py — one generic adapter for ANY Socrata (SODA) open-data portal.

Many large cities and states publish bids / solicitations / contracts on a
Socrata portal with a real JSON API and NO scraping required, e.g.:
    https://data.montgomerycountymd.gov/resource/eeq6-nnwe.json

You point this adapter at a dataset entirely from config.yaml:
  * domain      — e.g. data.montgomerycountymd.gov
  * dataset_id  — the 4x4 id, e.g. eeq6-nnwe
  * field_map   — which dataset columns map to our normalized fields
  * where/order/limit — optional SoQL to filter server-side

So adding another Socrata source is a config edit, not new code. An optional
Socrata app token (SOCRATA_APP_TOKEN secret) raises rate limits but is NOT
required — the tool works with zero keys.
"""

from __future__ import annotations

import os
import requests

from .base import Source
from ..models import Opportunity
from ..util import to_iso_date, clean_text


class SocrataSource(Source):
    """Config-driven Socrata/SODA reader. One instance per configured dataset."""

    def __init__(self, cfg: dict):
        # `cfg` is one entry from config.yaml -> sources.socrata_datasets[]
        self.name = cfg.get("name") or f"Socrata {cfg.get('dataset_id', '?')}"
        self.domain = cfg["domain"].replace("https://", "").replace("http://", "").strip("/")
        self.dataset_id = cfg["dataset_id"]
        self.state = cfg.get("state", "")
        self.field_map = cfg.get("field_map", {})
        self.where = cfg.get("where", "")
        self.order = cfg.get("order", "")
        self.limit = int(cfg.get("limit", 1000))
        # If a dataset has no URL column, build one from this template using the
        # raw row, e.g. "https://portal/bids?id={number}". Falls back to source_url.
        self.url_template = cfg.get("url_template", "")
        self.source_url = cfg.get("source_url", "")
        self.timeout = int(cfg.get("timeout", 45))

    def _endpoint(self) -> str:
        return f"https://{self.domain}/resource/{self.dataset_id}.json"

    def fetch(self) -> list[Opportunity]:
        params = {"$limit": self.limit}
        if self.where:
            params["$where"] = self.where
        if self.order:
            params["$order"] = self.order

        headers = {"Accept": "application/json"}
        # Optional app token (secret). Absent => still works, just lower limits.
        token = os.environ.get("SOCRATA_APP_TOKEN")
        if token:
            headers["X-App-Token"] = token

        resp = requests.get(self._endpoint(), params=params, headers=headers, timeout=self.timeout)
        resp.raise_for_status()
        rows = resp.json()

        opportunities: list[Opportunity] = []
        for row in rows:
            opp = self._row_to_opportunity(row)
            if opp is not None:
                opportunities.append(opp)
        return opportunities

    # -- helpers -----------------------------------------------------------

    def _get(self, row: dict, our_field: str, default: str = "") -> str:
        """Read a normalized field from a raw row using the configured mapping."""
        col = self.field_map.get(our_field)
        if not col:
            return default
        return clean_text(row.get(col, default))

    def _build_url(self, row: dict) -> str:
        # 1) explicit URL column, 2) template built from the row, 3) source homepage.
        direct = self._get(row, "url")
        if direct:
            return direct
        if self.url_template:
            try:
                return self.url_template.format(**row)
            except Exception:
                pass
        return self.source_url

    def _row_to_opportunity(self, row: dict):
        title = self._get(row, "title")
        # An untitled row is useless downstream; skip it (logged count still counts).
        if not title:
            return None

        # Build a stable id: prefer the source's own id column, else the title.
        raw_id = self._get(row, "id") or title
        return Opportunity(
            id=f"{self.dataset_id}:{raw_id}",
            source_name=self.name,
            title=title,
            description=self._get(row, "description") or title,
            agency=self._get(row, "agency"),
            state=self.state,
            url=self._build_url(row),
            naics=self._get(row, "naics"),
            psc=self._get(row, "psc"),
            set_aside=self._get(row, "set_aside"),
            posted_date=to_iso_date(self._get(row, "posted_date")),
            due_date=to_iso_date(self._get(row, "due_date")),
        )
