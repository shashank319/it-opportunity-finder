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

from abc import ABC, abstractmethod

from ..models import Opportunity


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


# ---------------------------------------------------------------------------
# MCP note (for later — do not build now):
# Because each adapter is just `name` + `fetch() -> list[Opportunity]`, wrapping
# one as an MCP server later is mechanical: expose a single MCP tool like
# `fetch_opportunities()` that calls the adapter and returns the same JSON shape
# `Opportunity.to_dict()` already produces. No pipeline coupling to untangle.
# ---------------------------------------------------------------------------
