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
from .samgov import SamGovSource
from .planetbids import PlanetBidsSource
from .opengov import OpenGovSource
from .arcgis import ArcGisSource


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

    # --- SAM.gov federal opportunities (the one federal source) ---
    samgov_cfg = sources_cfg.get("samgov", {})
    if samgov_cfg.get("enabled", False):
        sources.append(SamGovSource(samgov_cfg))

    # --- PlanetBids agency portals (one adapter, many agencies) ---
    planetbids_cfg = sources_cfg.get("planetbids", {})
    if planetbids_cfg.get("enabled", False):
        sources.append(PlanetBidsSource(planetbids_cfg))

    # --- OpenGov Procurement portals (one adapter, many agencies) ---
    opengov_cfg = sources_cfg.get("opengov", {})
    if opengov_cfg.get("enabled", False):
        sources.append(OpenGovSource(opengov_cfg))

    # --- ArcGIS feature layers (zero or more) ---
    for layer in sources_cfg.get("arcgis_layers", []):
        if layer.get("enabled", True):
            sources.append(ArcGisSource(layer))

    return sources
