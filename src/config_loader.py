"""
config_loader.py — loads config/config.yaml once and hands it around.

Everything a non-technical teammate might want to change (sources, keyword
lists, codes, scoring weights, email recipients) lives in config.yaml. This
module just reads that file. No logic or secrets here.
"""

from __future__ import annotations

import os
import yaml

# config.yaml sits at <repo>/config/config.yaml. We compute that path relative
# to this file so the tool works no matter what directory it is run from.
_HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_CONFIG_PATH = os.path.normpath(os.path.join(_HERE, "..", "config", "config.yaml"))


def load_config(path: str | None = None) -> dict:
    """Read config.yaml and return it as a plain dict.

    You can override the path with the CONFIG_PATH env var (used in tests).
    """
    path = path or os.environ.get("CONFIG_PATH") or _DEFAULT_CONFIG_PATH
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}
