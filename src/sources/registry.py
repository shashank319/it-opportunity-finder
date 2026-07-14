"""
registry.py — turns the `sources:` section of config.yaml into live adapters.

This is the ONE place that knows which adapter classes exist. To add a whole
new KIND of source later (e.g. an RSS adapter or a state scraper), you:
  1. write the adapter class (subclass of Source),
  2. add one line to build_sources() below.
Adding another instance of an EXISTING kind (e.g. another Socrata dataset)
needs no code — just a new config entry.
"""

from __future__ import annotations

from .socrata import SocrataSource
from .gmail_alerts import GmailAlertsSource


def build_sources(config: dict) -> list:
    """Read config['sources'] and return a list of enabled Source instances."""
    sources = []
    sources_cfg = config.get("sources", {})

    # --- Socrata open-data datasets (zero or more) ---
    for ds in sources_cfg.get("socrata_datasets", []):
        if ds.get("enabled", True):
            sources.append(SocrataSource(ds))

    # --- Gmail bid-alert ingestion (at most one) ---
    gmail_cfg = sources_cfg.get("gmail_alerts", {})
    if gmail_cfg.get("enabled", False):
        sources.append(GmailAlertsSource(gmail_cfg))

    return sources
