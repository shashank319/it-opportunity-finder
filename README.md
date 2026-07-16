# IT Opportunity Finder

Automatically discovers **state, county, and city (SLED)** IT / software project
opportunities every day, filters out non-IT physical work, dedupes them, and
shows the results two ways:

1. **A shared web dashboard** (static site on GitHub Pages), and
2. **A daily email digest** to your team.

It runs entirely on **free tiers** — GitHub Actions (scheduler), a committed
JSON file (data store), GitHub Pages (dashboard), and a free email provider.
**Baseline cost: $0/month.**

> **Scope:** state / county / city (SLED) sources, **plus an optional SAM.gov
> federal source** (tagged `US-FED`, toggled on/off in config). *Federal was added
> later at the owner's request; it can be disabled to return to SLED-only.*
> **Recall-first:** it catches broadly and only strips out clearly non-IT work.
> The score is for *sorting* — **your team decides what to bid.**

---

## How coverage works (three mechanisms)

You do **not** scrape tens of thousands of cities. Local coverage comes from:

1. **Email alerts from shared procurement platforms (primary).** You register
   (free) on PlanetBids, Bonfire, OpenGov, DemandStar, Periscope/BidSync,
   Public Purchase, Vendor Registry, BidNet, Ionwave, set keyword alerts, and
   they email a dedicated inbox. The tool reads those emails. → `gmail_alerts` adapter.
2. **Open-data feeds (Socrata) for big cities/states.** Clean JSON APIs, no
   scraping, no key. → `socrata` adapter. (Ships wired to a live county feed.)
3. **AI-scraping (later, off by default).** Only for the ~15 state portals with
   no feed and no alerts. **Not built yet** — that's Step 2.

---

## What's in this build (Step 1)

- Pluggable **Source adapter** interface (`src/sources/base.py`).
- **Generic Socrata adapter**, wired to a real, live, no-key county feed
  (**Montgomery County MD — Solicitations**).
- **Gmail bid-alert adapter** (read-only) for the shared platforms above.
- **Pipeline**: filter → score → dedupe (across sources *and* across days).
- **Static dashboard** with search + filters + sorting.
- **GitHub Action**: daily cron + manual "Run workflow" button + email digest.
- One **editable config file** for everything (`config/config.yaml`).

Not in this build (by design): the 15 state scrapers. Those come next, one at a time.

---

## Repo layout

```
config/config.yaml         ← the one file you edit (sources, keywords, codes, scoring)
src/
  main.py                  ← the daily runner (what the Action executes)
  models.py                ← the normalized Opportunity record
  pipeline.py              ← filter → score → dedupe
  history.py               ← cross-day dedupe memory
  email_digest.py          ← builds + sends the email (Resend / Brevo / SMTP)
  sources/
    base.py                ← Source interface
    registry.py            ← turns config into live adapters
    socrata.py             ← generic Socrata/SODA reader
    gmail_alerts.py        ← reads bid-alert emails
docs/                      ← the dashboard (GitHub Pages serves this folder)
  index.html · app.js · style.css
  opportunities.json       ← committed output the dashboard reads (public, no keys)
data/history.json          ← dedupe memory (committed)
scripts/gmail_setup.py     ← one-time local helper to authorize Gmail
.github/workflows/daily.yml← the scheduler
```

---

## Deployment — do this once

### 1. Put the code on GitHub
```bash
cd it-opportunity-finder
git add .
git commit -m "Initial IT Opportunity Finder"
# create an empty repo on github.com first, then:
git remote add origin https://github.com/<you>/it-opportunity-finder.git
git branch -M main
git push -u origin main
```
The repo can be **public** — the data is public procurement info and **no
secrets ever live in the repo** (they go in GitHub Actions Secrets, below).

### 2. Turn on the dashboard (GitHub Pages)
Repo **Settings → Pages** → *Build and deployment* → **Source: Deploy from a
branch** → **Branch: `main`**, **Folder: `/docs`** → Save.
After the first run your dashboard is at
`https://<you>.github.io/it-opportunity-finder/`.

### 3. Choose an email provider and add secrets
Repo **Settings → Secrets and variables → Actions → New repository secret**.
Add `EMAIL_PROVIDER`, `EMAIL_TO` (comma-separated), `EMAIL_FROM`, plus the keys
for the provider you picked:

| Provider | Set `EMAIL_PROVIDER` to | Also add these secrets |
|----------|-------------------------|------------------------|
| **Resend** (easiest) | `resend` | `RESEND_API_KEY` |
| **Brevo** | `brevo` | `BREVO_API_KEY` |
| **Gmail SMTP** | `smtp` | `SMTP_USER`, `SMTP_PASS` (a Gmail *App Password*), optional `SMTP_HOST`/`SMTP_PORT` |

> With Resend/Brevo, `EMAIL_FROM` must be an address on a domain you've verified
> in that provider. With Gmail SMTP, use your Gmail address and a 16-char App
> Password (Google Account → Security → App passwords), **not** your login password.

Email is optional — if you skip it, the dashboard still updates daily.

### 4. Run it the first time
Repo **Actions** tab → **Daily IT Opportunity Run** → **Run workflow**.
It fetches, filters, commits `docs/opportunities.json`, and emails the digest.
Refresh your Pages URL to see results. After this, it runs itself daily
(12:00 UTC — change the `cron` in `.github/workflows/daily.yml` to retime).

That's it. Everything below is optional tuning.

---

## Optional: turn on email bid-alerts (the primary local coverage)

This lets the tool read a **dedicated Gmail inbox** full of alert emails from the
shared platforms. Read-only; no password is stored.

1. **Make a dedicated Gmail** (e.g. `yourfirm.bids@gmail.com`). Register on the
   shared platforms with it and set up keyword/saved-search alerts.
2. **Enable the Gmail API + make an OAuth client:**
   - Go to <https://console.cloud.google.com/> → create a project.
   - **APIs & Services → Library** → enable **Gmail API**.
   - **APIs & Services → Credentials → Create credentials → OAuth client ID** →
     application type **Desktop app** → download the JSON.
3. **Authorize once on your laptop:**
   ```bash
   cd it-opportunity-finder
   pip install -r requirements.txt
   # save the downloaded JSON as scripts/client_secret.json
   python scripts/gmail_setup.py
   ```
   A browser opens; log in as the **dedicated inbox**, approve **read-only**
   access. The script prints a token blob.
4. **Store it as a secret:** copy the whole blob into a new secret named
   **`GMAIL_TOKEN_JSON`**.
5. **Enable the source:** in `config/config.yaml` set
   `sources.gmail_alerts.enabled: true` and commit.

The per-platform sender addresses and link patterns are in the same config
block — add a new alert provider by adding a `platforms:` entry, no code needed.
As real alert emails arrive, tune each platform's `link_contains` / optional
`deadline_regex` to parse them precisely. Until then the adapter falls back to
one opportunity per alert email so nothing is lost.

---

## Optional: SAM.gov federal source

Federal opportunities from SAM.gov are enabled in `config.yaml`
(`sources.samgov.enabled: true`) but need a free API key:

1. Sign in at <https://sam.gov/>, open your **Account Details → API Key**, and
   generate a **Public API key**.
2. Add it as a GitHub Actions secret named **`SAM_API_KEY`**.

Every SAM.gov record is tagged `state = US-FED`, so on the dashboard you can
show or hide federal with the **State** filter. To turn federal off entirely,
set `sources.samgov.enabled: false`. Rate limits are modest, so keep the
`naics_codes` list short (the tool makes one request per code per day).

## Adding more agencies: PlanetBids & OpenGov

Two adapters pull directly from procurement-platform portals (public, no login):

- **PlanetBids** (`sources.planetbids.agencies`) — add an agency by its numeric
  portalId (the number in `vendors.planetbids.com/portal/<portalId>/...`).
- **OpenGov** (`sources.opengov.agencies`) — add an agency by its slug (the name
  in `procurement.opengov.com/portal/<slug>`).

Both platforms are used by thousands of governments, so you grow coverage just
by adding entries — no code changes. Each entry is one agency. (These hit public
portal endpoints; if a platform changes its site, that source is logged and
skipped without affecting the rest.)

**ArcGIS layers** (`sources.arcgis_layers`) work like Socrata: give it a layer
`query_url` + a `field_map`. Ships with Washington, D.C.'s live solicitations.

## Optional: add another Socrata open-data feed

Find a dataset (many cities/states publish bids/solicitations/contracts):
```
https://api.us.socrata.com/api/catalog/v1?q=solicitation&only=dataset
```
Copy a block under `sources.socrata_datasets` in `config/config.yaml`, set
`domain`, `dataset_id`, `state`, and map that dataset's columns to our fields in
`field_map`. Set `enabled: true`. No key needed; add the optional
`SOCRATA_APP_TOKEN` secret only if you hit rate limits.

---

## Tuning what's included/excluded

All of this is in `config/config.yaml` — no code changes:

- **`filtering.include_keywords`** — an item is kept if title/description hits
  any of these (whole-word match, so "app" matches *app* but not *Apparel*).
- **`filtering.include_codes`** — NAICS / UNSPSC / NIGP codes that count as IT.
- **`filtering.exclude_keywords`** — clearly non-IT physical work. An item is
  dropped only if it hits one of these **and** none of…
- **`filtering.strong_software_keywords`** — unambiguous "this is software"
  terms that override an exclude (keep + let a human decide).
- **`scoring.*`** — the 0-100 sort score weights (tier-1 = your sweet spot:
  Laserfiche, Mendix, .NET, cloud, migration…).

---

## Adding a brand-new *kind* of source (for a developer)

1. Create `src/sources/mysource.py` with a class that subclasses `Source` and
   implements `fetch() -> list[Opportunity]` (see `socrata.py` as a template).
2. Add one line in `src/sources/registry.py` to build it from config.
3. Add its config block under `sources:` in `config.yaml`.

A source that raises is **logged and skipped** — it can never crash the daily
run or affect the other sources.

### MCP note (later)
Each adapter is just `name` + `fetch() -> list[Opportunity]`, so exposing one as
an **MCP server** later is mechanical: wrap `fetch()` in a single MCP tool that
returns the same JSON `Opportunity.to_dict()` already produces. Nothing in the
pipeline needs to change. Not built now — just designed to allow it.

---

## Optional: Claude classifier for borderline items

`config.yaml → claude_classifier.enabled` is **false** by default, so the
baseline is $0 with pure keyword/code filtering. (Wiring is a later step; the
flag exists now so the config shape is stable.)

---

## Security & cost guarantees

- **No secrets in the repo or the dashboard.** All keys live only in GitHub
  Actions Secrets and are used only inside the Action run. `main.py` has a
  safety check that refuses to write `opportunities.json` if it looks like it
  contains anything key-shaped.
- **$0 baseline.** Socrata + email-alert ingestion are free. The only
  paid-risk items (AI-scraping, Claude classifier) are **off by default** and
  clearly flagged.
- **Resilient.** One source failing never stops the others or crashes the run.
- **Public listings only.** No automated login; you download docs and submit
  bids manually.

---

## Run it locally (for testing)

```bash
cd it-opportunity-finder
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m src.main --no-email      # fetch + filter + write JSON, skip email
# then preview the dashboard:
python3 -m http.server 8000 --directory docs   # open http://localhost:8000
```
