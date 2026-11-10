"""
base.py — the common Source interface every adapter implements.

Design goals (from the project spec):
  * Adding a new source = add a new adapter file + a config entry. Nothing else.
  * A single broken source must be LOGGED and SKIPPED — never crash the run.
  * Each adapter is cleanly decoupled so it could later be exposed as an MCP
    (Model Context Protocol) server. See the MCP note in the README.

An adapter only has to do two things:
  1. Know its own display `name`.
  2. Implement `fetch()` -> list[Opportunity].

The runner in main.py wraps every `fetch()` in try/except and records health,
so adapters can stay simple and just raise on failure.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod

import requests

from ..models import Opportunity

# Errors worth one retry: the site was slow, briefly down, or rate-limiting us.
_RETRY_STATUS = {429, 500, 502, 503, 504}
_RETRY_DELAY_SECONDS = 5


class Source(ABC):
    """Base class for all opportunity sources."""

    # Human-readable name shown in the dashboard/email and used in health logs.
    name: str = "unnamed-source"

    @abstractmethod
    def fetch(self) -> list[Opportunity]:
        """Return a list of normalized Opportunity records.

        Raise on failure — main.py catches it, logs it, and continues with the
        other sources. Do NOT swallow errors silently inside an adapter, or a
        broken source will look "healthy" while returning nothing.
        """
        raise NotImplementedError

    def fetch_each(self, agencies: list, fetch_one) -> list[Opportunity]:
        """Run `fetch_one(agency)` for every enabled agency of a multi-agency
        platform, so ONE broken agency can't take down all the others.

        A transient failure (timeout, connection error, 429/5xx) is retried
        once. An agency that still fails is skipped and recorded in
        `self.agency_errors` (main.py shows these in the health log). Raises
        only if EVERY agency failed, so a fully broken source still shows FAIL.
        Set `self.agency_delay` (seconds) to pause between agencies on
        platforms that rate-limit one host serving many tenants.
        """
        self.agency_errors: list[str] = []
        out: list[Opportunity] = []
        attempted = 0
        first_error = None
        for ag in agencies:
            if not ag.get("enabled", True):
                continue
            if attempted and getattr(self, "agency_delay", 0):
                time.sleep(self.agency_delay)
            attempted += 1
            try:
                out.extend(_with_retry(fetch_one, ag))
            except Exception as e:
                label = ag.get("agency") or next(iter(ag.values()), "?")
                self.agency_errors.append(f"{label}: {type(e).__name__}: {e}")
                first_error = first_error or e
        if attempted and len(self.agency_errors) == attempted:
            raise first_error
        return out


def _with_retry(fetch_one, ag):
    try:
        return fetch_one(ag)
    except (requests.Timeout, requests.ConnectionError):
        pass
    except requests.HTTPError as e:
        if e.response is None or e.response.status_code not in _RETRY_STATUS:
            raise
    time.sleep(_RETRY_DELAY_SECONDS)
    return fetch_one(ag)


# ---------------------------------------------------------------------------
# MCP note (for later — do not build now):
# Because each adapter is just `name` + `fetch() -> list[Opportunity]`, wrapping
# one as an MCP server later is mechanical: expose a single MCP tool like
# `fetch_opportunities()` that calls the adapter and returns the same JSON shape
# `Opportunity.to_dict()` already produces. No pipeline coupling to untangle.
# ---------------------------------------------------------------------------
