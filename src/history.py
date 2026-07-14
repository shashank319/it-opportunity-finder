"""
history.py — remembers which opportunities we've already seen, so we can:
  * DEDUPE across days (don't re-show the same bid every morning), and
  * flag which items are NEW since the last run (for the "new" badge + email).

Storage is a single committed JSON file (data/history.json). No database.
Shape:  { "<dedupe_key>": {"first_seen": "YYYY-MM-DD"}, ... }
Old entries are pruned after `retention_days` so the file doesn't grow forever.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_HISTORY_PATH = os.path.normpath(os.path.join(_HERE, "..", "data", "history.json"))


def load_history(path: str | None = None) -> dict:
    path = path or _DEFAULT_HISTORY_PATH
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        # A corrupt history file should never crash the run — start fresh.
        return {}


def save_history(history: dict, path: str | None = None, retention_days: int = 180) -> None:
    path = path or _DEFAULT_HISTORY_PATH
    pruned = _prune(history, retention_days)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(pruned, f, indent=2, sort_keys=True)


def _prune(history: dict, retention_days: int) -> dict:
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=retention_days)).isoformat()
    return {k: v for k, v in history.items() if v.get("first_seen", "9999-99-99") >= cutoff}
