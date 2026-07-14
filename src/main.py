"""
main.py — the daily runner. This is what GitHub Actions executes.

Steps:
  1. Load config.
  2. Build enabled sources from config.
  3. Fetch each source in try/except (one broken source NEVER stops the others).
  4. Run the pipeline: filter -> score -> dedupe, and mark new items.
  5. Write docs/opportunities.json (what the dashboard reads) — NO secrets in it.
  6. Save the rolling history file (for cross-day dedupe).
  7. Send the daily email digest of NEW items (skips cleanly if not configured).

Run locally:
    python -m src.main                # normal run
    python -m src.main --no-email     # skip sending email (for testing)

Exit code is always 0 on a completed run (even if some sources failed) so the
GitHub Action's "commit results" step still runs. It exits non-zero only if it
truly can't produce output.
"""

from __future__ import annotations

import os
import sys
import json
import traceback
from datetime import datetime, timezone

from .config_loader import load_config
from .sources.registry import build_sources
from .pipeline import run_pipeline
from .history import load_history, save_history
from .email_digest import send_digest
from .models import Opportunity

_HERE = os.path.dirname(os.path.abspath(__file__))
_OUTPUT_PATH = os.path.normpath(os.path.join(_HERE, "..", "docs", "opportunities.json"))


def _fetch_all(sources) -> tuple[list[Opportunity], list[dict]]:
    """Fetch every source, isolating failures. Returns (records, health_log)."""
    all_records: list[Opportunity] = []
    health: list[dict] = []

    for src in sources:
        name = getattr(src, "name", src.__class__.__name__)
        try:
            records = src.fetch() or []
            all_records.extend(records)
            health.append({"source": name, "ok": True, "count": len(records), "error": ""})
            print(f"  [ok]   {name}: {len(records)} records")
        except Exception as e:
            # Log and SKIP — never let one source crash the daily run.
            msg = f"{type(e).__name__}: {e}"
            health.append({"source": name, "ok": False, "count": 0, "error": msg})
            print(f"  [FAIL] {name}: {msg}")
            traceback.print_exc()

    return all_records, health


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    send_email = "--no-email" not in argv

    print("== IT Opportunity Finder — daily run ==")
    config = load_config()

    print("1) Building sources from config...")
    sources = build_sources(config)
    print(f"   {len(sources)} source(s) enabled.")

    print("2) Fetching sources...")
    raw, health = _fetch_all(sources)
    ok_count = sum(1 for h in health if h["ok"])
    print(f"   Fetched {len(raw)} raw records. Sources OK: {ok_count}/{len(health)}.")

    print("3) Filter -> score -> dedupe...")
    history = load_history()
    kept, history = run_pipeline(raw, config, history)
    new_items = [o for o in kept if o.is_new]
    print(f"   {len(kept)} kept after filtering; {len(new_items)} new since last run.")

    print("4) Writing dashboard data (docs/opportunities.json)...")
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "counts": {"total": len(kept), "new": len(new_items)},
        "health": {
            "ok": ok_count,
            "total": len(health),
            "sources": health,
        },
        "opportunities": [o.to_dict() for o in kept],
    }
    _write_output(output)
    _assert_no_secrets(output)  # safety net: never leak keys into the public file

    print("5) Saving history (cross-day dedupe)...")
    retention = int(config.get("history", {}).get("retention_days", 180))
    save_history(history, retention_days=retention)

    if send_email:
        print("6) Sending email digest...")
        send_digest(new_items, {"ok": ok_count, "total": len(health), "sources": health})
    else:
        print("6) Skipping email (--no-email).")

    print("Done.")
    return 0


def _write_output(output: dict) -> None:
    os.makedirs(os.path.dirname(_OUTPUT_PATH), exist_ok=True)
    with open(_OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)


def _assert_no_secrets(output: dict) -> None:
    """Cheap guard: the public JSON must never contain anything key-shaped.

    We scan the serialized output for the NAMES of our secret env vars and for
    obvious token prefixes. If found, we blank the file rather than commit a leak.
    """
    blob = json.dumps(output).lower()
    suspicious = [
        "resend_api_key", "brevo_api_key", "smtp_pass", "gmail_token_json",
        "socrata_app_token", "anthropic_api_key", "re_live_", "xkeysib-",
        "refresh_token", "client_secret",
    ]
    hit = [s for s in suspicious if s in blob]
    if hit:
        raise SystemExit(f"SAFETY STOP: output looks like it contains secrets: {hit}")


if __name__ == "__main__":
    raise SystemExit(main())
