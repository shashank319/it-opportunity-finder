"""
email_digest.py — builds and sends the daily digest email.

Supports three free providers, chosen by the EMAIL_PROVIDER env var:
  * resend  — https://resend.com  (needs RESEND_API_KEY)
  * brevo   — https://brevo.com   (needs BREVO_API_KEY)
  * smtp    — any SMTP incl. Gmail (needs SMTP_HOST/PORT/USER/PASS)

Recipients come from EMAIL_TO (comma-separated). Sender from EMAIL_FROM.
If email is not configured, we log a warning and skip — the run still
succeeds and the dashboard still updates.

Subject:  "IT Opportunity Finder — X new opportunities (DATE)"
Body:     new-since-yesterday items, grouped by state/source, each with
          title, agency, source, deadline, link. Plus a health line:
          "Sources fetched today: N/N OK."
"""

from __future__ import annotations

import os
import html
import smtplib
from email.mime.text import MIMEText
from collections import defaultdict

import requests

from .models import Opportunity
from .util import today_iso


def build_digest_html(new_items: list[Opportunity], health: dict) -> str:
    """Build the HTML body. `health` = {"ok": n, "total": m, "sources": [...]}."""
    ok, total = health.get("ok", 0), health.get("total", 0)

    if not new_items:
        body = "<p>No new opportunities today.</p>"
    else:
        # Group by state, then source, for a scannable digest.
        by_state: dict[str, list[Opportunity]] = defaultdict(list)
        for o in new_items:
            by_state[o.state or "Unknown state"].append(o)

        chunks = []
        for state in sorted(by_state.keys()):
            items = sorted(by_state[state], key=lambda o: (-o.it_score, o.due_date or "9999"))
            rows = []
            for o in items:
                title = html.escape(o.title)
                link = html.escape(o.url or "#")
                agency = html.escape(o.agency or "—")
                source = html.escape(o.source_name)
                due = html.escape(o.due_date or "—")
                rows.append(
                    f'<tr>'
                    f'<td style="padding:6px 10px;"><a href="{link}">{title}</a><br>'
                    f'<span style="color:#666;font-size:12px;">{agency} · {source}</span></td>'
                    f'<td style="padding:6px 10px;white-space:nowrap;">Due: {due}</td>'
                    f'<td style="padding:6px 10px;text-align:right;">{o.it_score}</td>'
                    f'</tr>'
                )
            chunks.append(
                f'<h3 style="margin:18px 0 4px;">{html.escape(state)} '
                f'<span style="color:#888;font-weight:normal;">({len(items)})</span></h3>'
                f'<table style="border-collapse:collapse;width:100%;font-size:14px;">'
                f'<tr style="color:#888;font-size:12px;text-align:left;">'
                f'<th style="padding:4px 10px;">Opportunity</th>'
                f'<th style="padding:4px 10px;">Deadline</th>'
                f'<th style="padding:4px 10px;text-align:right;">Score</th></tr>'
                + "".join(rows) + '</table>'
            )
        body = "".join(chunks)

    return f"""<div style="font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;max-width:720px;margin:auto;color:#222;">
  <h2 style="margin-bottom:2px;">IT Opportunity Finder</h2>
  <p style="color:#555;margin-top:0;">Sources fetched today: <b>{ok}/{total} OK</b></p>
  {body}
  <hr style="margin-top:24px;border:none;border-top:1px solid #eee;">
  <p style="color:#999;font-size:12px;">State &amp; local (SLED) opportunities only. Scores are for sorting; your team decides what to bid.</p>
</div>"""


def build_subject(new_items: list) -> str:
    return f"IT Opportunity Finder — {len(new_items)} new opportunities ({today_iso()})"


def send_digest(new_items: list[Opportunity], health: dict) -> bool:
    """Send the digest via the configured provider. Returns True if sent."""
    provider = (os.environ.get("EMAIL_PROVIDER") or "").strip().lower()
    recipients = [r.strip() for r in (os.environ.get("EMAIL_TO") or "").split(",") if r.strip()]
    sender = os.environ.get("EMAIL_FROM", "")

    if not provider or not recipients:
        print("  [email] EMAIL_PROVIDER or EMAIL_TO not set — skipping email (dashboard still updated).")
        return False

    subject = build_subject(new_items)
    html_body = build_digest_html(new_items, health)

    try:
        if provider == "resend":
            return _send_resend(sender, recipients, subject, html_body)
        elif provider == "brevo":
            return _send_brevo(sender, recipients, subject, html_body)
        elif provider == "smtp":
            return _send_smtp(sender, recipients, subject, html_body)
        else:
            print(f"  [email] Unknown EMAIL_PROVIDER '{provider}' — skipping.")
            return False
    except Exception as e:
        # Email failing must not fail the whole run.
        print(f"  [email] Send failed: {e}")
        return False


def _send_resend(sender, recipients, subject, html_body) -> bool:
    key = os.environ["RESEND_API_KEY"]
    resp = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"from": sender, "to": recipients, "subject": subject, "html": html_body},
        timeout=30,
    )
    resp.raise_for_status()
    print(f"  [email] Sent via Resend to {len(recipients)} recipient(s).")
    return True


def _send_brevo(sender, recipients, subject, html_body) -> bool:
    key = os.environ["BREVO_API_KEY"]
    resp = requests.post(
        "https://api.brevo.com/v3/smtp/email",
        headers={"api-key": key, "Content-Type": "application/json"},
        json={
            "sender": {"email": sender},
            "to": [{"email": r} for r in recipients],
            "subject": subject,
            "htmlContent": html_body,
        },
        timeout=30,
    )
    resp.raise_for_status()
    print(f"  [email] Sent via Brevo to {len(recipients)} recipient(s).")
    return True


def _send_smtp(sender, recipients, subject, html_body) -> bool:
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ["SMTP_USER"]
    password = os.environ["SMTP_PASS"]

    msg = MIMEText(html_body, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = sender or user
    msg["To"] = ", ".join(recipients)

    with smtplib.SMTP(host, port, timeout=30) as server:
        server.starttls()
        server.login(user, password)
        server.sendmail(msg["From"], recipients, msg.as_string())
    print(f"  [email] Sent via SMTP ({host}) to {len(recipients)} recipient(s).")
    return True
