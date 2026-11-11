"""
bid_page.py — ONE generic heuristic adapter for custom city/county bid pages.

Many local governments publish their open solicitations on a hand-built page
of their own CMS (not Bonfire/OpenGov/BidNet/...). Writing a one-off scraper
for each is unmaintainable, so this adapter applies conservative heuristics to
any server-rendered page and pulls out the entries that look like real
solicitations:

  * candidate = a link plus its "row" (the nearest table row / list item /
    short paragraph / short block around it)
  * a row scores points for solicitation words (RFP, RFQ, IFB, ITB, Bid,
    Proposal, Solicitation, ...), a bid-number pattern, sitting in a table
    whose header looks like a bid table, a date, linking to a document
  * nav / header / footer / menus / sidebars / social links are stripped first,
    and sections headed "Awarded", "Closed", "Bid Results", "Archive", ... are
    dropped where the page labels them
  * rows saying awarded/closed/cancelled, and rows whose due date is already
    past, are dropped
  * generic info links ("Bid Results", "Vendor Registration", "How to bid") are
    rejected outright

It is deliberately conservative: fewer real items beats nav junk. When a page
needs help, give per-agency hints in config:

  - type: bid_page
    name: "Custom bid pages"
    agencies:
      - url: https://example.gov/bids
        agency: "City of Example"
        state: "XX"
        container_selector: "div.main-content table"   # only look inside these
        link_selector: "td.title a"                      # every match is an entry
        exclude_selector: "#closed-bids"                 # remove before parsing
        keep_past: false                                 # keep rows with past due dates
        require_due: false                               # drop entries with no parseable due date
        min_score: 3                                     # override the threshold

Public pages only: plain anonymous GET, no login, and no attempt to get past
bot challenges (a 403 / challenge page simply raises and is logged).
"""

from __future__ import annotations

import hashlib
import re
from datetime import date
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .base import Source
from ..models import Opportunity
from ..util import to_iso_date, clean_text, today_iso

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

# --- Page chrome to strip ---------------------------------------------------
_CHROME_TAGS = ["script", "style", "noscript", "nav", "header", "footer", "aside",
                "iframe", "svg", "button", "select"]
_CHROME_RE = re.compile(
    r"(^|[-_\s])(nav|navbar|navigation|menu|megamenu|submenu|footer|breadcrumbs?|"
    r"sidebar|side-bar|sidenav|social|share|sharing|skip|cookie|banner|"
    r"quicklinks?|quick-links|search|emergency|translate|"
    r"utility|toolbar|subscribe|newsletter|pagination|pager)([-_\s]|$)",
    re.I,
)
_CHALLENGE_RE = re.compile(r"just a moment|attention required|access denied|"
                           r"checking your browser|are you a robot|captcha", re.I)

# --- Solicitation signals -----------------------------------------------------
_KEYWORD_RE = re.compile(
    r"\b(RFP|RFQ|RFI|RFB|IFB|ITB|RFSQ|RFQu|RFPQ|SOQ|IFQ|EOI|"
    r"bids?|re-?bid|proposals?|solicitations?|quotes?|quotations?|tenders?|"
    r"request\s+for|invitation\s+(to|for)|qualifications|sealed)\b",
    re.I,
)
# e.g. "RFP 2026-014", "Bid #26-03", "No. 2026-17", "IFB-25-0112", "26-1234"
_BIDNO_RE = re.compile(
    r"(?:\b(?:RFP|RFQ|RFI|RFB|IFB|ITB|RFSQ|SOQ|EOI|bid|solicitation|contract|project)s?\b"
    r"\s*(?:no\.?|number|#)?\s*[:#]?\s*((?:[A-Z]{1,6}-?)?\d[\w.\-/]{1,20}))"
    r"|(?:(?:no\.?|#)\s*([A-Z]{0,6}-?\d[\w.\-/]{1,20}))"
    r"|\b((?:[A-Z]{1,6}-)?(?:19|20)?\d{2}-\d{2,5}(?:-[A-Z0-9]{1,6})?)\b"
    r"|\b([A-Z]{0,2}\d{4,8}-\d{2,5})\b"
    r"|\b((?=[A-Z0-9-]*\d[A-Z0-9-]*\d[A-Z0-9-]*\d)[A-Z0-9]{1,8}(?:-[A-Z0-9]{1,8}){1,4})\b",
    re.I,
)
_DEAD_RE = re.compile(
    r"\b(awarded|award\s+(notice|recommendation|letter)|notice\s+of\s+(intent\s+to\s+)?award|"
    r"closed|cancel+ed|cancel+ation|rescinded|withdrawn|archived?|expired|"
    r"bid\s+results?|tabulations?|bid\s+tabs?|intent\s+to\s+award|no\s+longer)\b",
    re.I,
)
# Section headings that introduce non-current listings.
_DEAD_SECTION_RE = re.compile(
    r"\b(awarded|award(s|ed)?\s+(bids|contracts)|closed|past|previous|archive[sd]?|expired|"
    r"results?|tabulations?|cancel+ed|historical|prior)\b",
    re.I,
)
# Link / title text that is page furniture rather than a solicitation.
_GENERIC_TITLE_RE = re.compile(
    r"^\s*((fy\s*)?\d{4}(-\d{2,4})?\s+)?((current|open|active|formal|informal|upcoming|public|all|view|see|search|"
    r"city|county|construction)\s+)*"
    r"(bids?|rfps?|rfqs?|solicitations?|opportunit(y|ies)|bid\s+opportunit(y|ies)|"
    r"bids?\s*(,|and|&|/)\s*(rfps?|proposals?|quotes?|rfqs?)(\s*(,|and|&|/)\s*\w+)?|"
    r"procurement|purchasing|requests?\s+for\s+(proposals?|bids?|quotes?|qualifications)|"
    r"bid\s+(board|list|calendar|documents?|notices?)|notices?|here|click\s+here|"
    r"more|read\s+more|learn\s+more|details?|view|download|pdf|link|documents?)\s*[.!:>»]*\s*$",
    re.I,
)
_INFO_RE = re.compile(
    r"\b(register|registration|vendor\s+(portal|list|application|guide|login|self)|"
    r"how\s+to|sign\s*up|subscribe|log\s*in|login|policy|policies|manual|handbook|"
    r"terms\s+and\s+conditions|instructions\s+to\s+bidders|general\s+conditions|faq|"
    r"frequently\s+asked|contact\s+us|ordinance|guidelines?|w-?9|"
    r"insurance\s+requirements|doing\s+business|business\s+with|mbe|wbe|dbe|"
    r"disadvantaged|surplus|auction|bid\s+results?|tabulation|bid\s+tabs?|awarded|"
    r"cooperative|piggyback|prevailing\s+wage|certification|forms?\b|plan\s*room|"
    r"notify\s+me|alert\s+center|email\s+updates|bonfire|opengov|bidnet|demandstar|"
    r"planetbids|ionwave|public\s*purchase|questcdn|bids\s*&\s*tenders|commbuys|emma\b|"
    r"eva\b|bidexpress|bid\s+limit|budget\s+adjustment|w-9)\b",
    re.I,
)
# Supporting documents of a bid rather than the bid itself.
_SUPPORT_DOC_RE = re.compile(
    r"\b(addend(um|a)|q\s*&\s*a|questions?\s+(and|&)\s+answers?|clarifications?|"
    r"award|bid\s+tab|plan\s*holders?|sign[-\s]?in|minutes|pre-?bid|pre-?proposal|"
    r"attachments?|exhibits?|appendix|affidavit|bid\s+form|price\s+sheet|"
    r"extension|errata|notice\s+of\s+intent|rejection|revised|bid\s+docs?|"
    r"specifications\s+and|contract\s+documents|amendment|assignment|^\s*agreement|"
    r"template|rate\s+sheet|"
    r"no\s+bids\s+received|non[-\s]?compliant|multiple\s+awards|being\s+rebid)\b",
    re.I,
)
_SOCIAL_HOSTS = ("facebook.", "twitter.", "x.com", "instagram.", "linkedin.", "youtube.",
                 "nextdoor.", "tiktok.", "pinterest.", "flickr.", "govdelivery.",
                 "granicus.", "google.com/maps", "apple.com", "adobe.com")
_DOC_RE = re.compile(r"\.(pdf|docx?|xlsx?|zip)(\?|#|$)|/documentcenter/|/archivecenter/|"
                     r"/bids\.aspx\?bidid=|download|/files?/|/media/|/uploads/", re.I)

_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_DATE_RE = re.compile(
    r"\b\d{1,2}/\d{1,2}/(?:\d{4}|\d{2})\b"
    r"|\b\d{4}-\d{2}-\d{2}\b"
    rf"|\b{_MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}\b"
    rf"|\b\d{{1,2}}\s+{_MONTH}\s+\d{{4}}\b",
    re.I,
)
_DUE_CUE_RE = re.compile(r"\b(due|clos(e|es|ing)|deadline|opening|opens?|submi(ssion|t)|"
                         r"receiv(ed|ing)|respon(se|ses)|until|no\s+later)\b", re.I)
_STRONG_CUE_BEFORE_RE = re.compile(
    r"((bids?|proposals?|quotes?|responses?|submissions?|soqs?|qualifications)\s+(are\s+)?"
    r"(due|must\s+be\s+received|received)|clos(e|es|ing)(\s+date)?|deadline|due\s+date)"
    r"[^.]{0,25}$", re.I)
_STRONG_CUE_AFTER_RE = re.compile(
    r"^[\s:\-–(at\d.apmAPM)]*(bids?|proposals?|quotes?|responses?|submissions?)\s+(are\s+)?due", re.I)
_BID_TABLE_HEAD_RE = re.compile(r"\b(bid|solicitation|rfp|rfq|title|description|project|"
                                r"due|closing|opening|deadline)\b", re.I)

_ROW_TAGS = {"tr", "li", "p", "dd", "dt", "article", "h2", "h3", "h4", "h5", "h6"}
_BLOCK_TAGS = {"div", "section", "td"}
_MAX_ROW_CHARS = 450
_DEFAULT_MIN_SCORE = 3


class BidPageSource(Source):
    """Heuristic reader for custom (non-platform) government bid listing pages."""

    def __init__(self, cfg: dict):
        self.name = cfg.get("name", "Custom bid pages")
        self.agencies = cfg.get("agencies", [])
        self.timeout = int(cfg.get("timeout", 30))

    def fetch(self) -> list[Opportunity]:
        return self.fetch_each(self.agencies, self._fetch_agency)

    # ------------------------------------------------------------------ fetch
    def _fetch_agency(self, ag: dict) -> list[Opportunity]:
        url = ag["url"]
        resp = requests.get(
            url,
            headers={"User-Agent": _BROWSER_UA,
                     "Accept": "text/html,application/xhtml+xml",
                     "Accept-Language": "en-US,en;q=0.9"},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return self.parse(resp.text, resp.url or url, ag)

    # ------------------------------------------------------------------ parse
    def parse(self, html: str, page_url: str, ag: dict) -> list[Opportunity]:
        """Extract opportunities from already-fetched HTML (handy for tests)."""
        soup = BeautifulSoup(html, "html.parser")
        title_el = soup.find("title")
        if title_el and _CHALLENGE_RE.search(title_el.get_text() or ""):
            raise RuntimeError("bot-challenge page served; skipping (not bypassing)")

        agency_name = ag.get("agency", page_url)
        state = ag.get("state", "")
        keep_past = bool(ag.get("keep_past", False))
        min_score = int(ag.get("min_score", _DEFAULT_MIN_SCORE))
        hinted = bool(ag.get("link_selector"))
        require_due = bool(ag.get("require_due", False))

        for sel in filter(None, [ag.get("exclude_selector")]):
            for el in soup.select(sel):
                el.decompose()
        if not ag.get("keep_chrome"):
            _strip_chrome(soup)
        _drop_dead_sections(soup)

        scopes = soup.select(ag["container_selector"]) if ag.get("container_selector") else []
        if not scopes:
            main = soup.find("main") or soup.find(attrs={"role": "main"})
            if main is None or len(main.get_text(" ", strip=True)) < 200:
                main = soup.body or soup
            scopes = [main]

        links = []
        for scope in scopes:
            links.extend(scope.select(ag["link_selector"]) if hinted
                         else scope.find_all("a", href=True))

        page_host = urlparse(page_url).netloc.lower()
        today = today_iso()
        seen_rows, seen_urls, seen_bids = set(), set(), set()
        out: list[Opportunity] = []
        for a in links:
            href = (a.get("href") or "").strip()
            if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")) or "/cdn-cgi/" in href:
                continue
            abs_url = urljoin(page_url, href)
            if abs_url.split("#")[0] == page_url.split("#")[0]:
                continue
            if any(s in abs_url.lower() for s in _SOCIAL_HOSTS):
                continue

            row = _row_for(a)
            if id(row) in seen_rows:
                continue
            row_text = clean_text(row.get_text(" "))
            link_text = clean_text(a.get_text(" "))
            if link_text and _SUPPORT_DOC_RE.match(link_text) and not hinted:
                continue  # "Addendum #2", "Q&A", "Award letter" ... belong to another entry
            title = _pick_title(a, row, link_text, row_text)
            if not title:
                continue

            if not hinted:
                score = _score(a, row, title, row_text, abs_url)
                if score < min_score:
                    continue
                # Keyword + list item alone is how nav links look; need something specific.
                if not (_bid_number(title) or _bid_number(row_text) or _DATE_RE.search(row_text)
                        or _DOC_RE.search(abs_url) or (row.name == "tr" and _in_bid_table(row))):
                    continue
                if _GENERIC_TITLE_RE.match(title) or _INFO_RE.search(title):
                    continue
                if _SUPPORT_DOC_RE.search(title) or re.search(r"\bFY\s*\d", title):
                    continue
                if not (row.name == "tr" and _in_bid_table(row)) and len(title.split()) < 4 \
                        and not _KEYWORD_RE.search(title) and not _bid_number(title):
                    continue
                if a.find_parent(["th", "thead"]) or re.search(r"[?&](order|sort|page)=", abs_url):
                    continue
                leaf = urlparse(abs_url).path.rstrip("/").rsplit("/", 1)[-1]
                if re.search(r"award|minutes|tabulation|bid[-_]?tab|results", leaf, re.I):
                    continue
                if _stale_year(abs_url, title):
                    continue
            if _DEAD_RE.search(row_text) and not re.search(r"\bopen\b", row_text, re.I):
                continue

            due = ""
            if row.name == "tr":
                due = _due_date(_cell_by_header(row, r"due|clos|deadline|opening|submi"))
            due = due or _due_date(row_text) or _due_date(_context_text(row), cue_only=True)
            if due and due < today and not keep_past:
                continue
            if require_due and not due:
                continue
            if abs_url in seen_urls:
                continue
            seen_rows.add(id(row))
            seen_urls.add(abs_url)

            bid_no = _bid_number(title) or _bid_number(row_text)
            if bid_no and bid_no.upper() in seen_bids:
                continue
            if bid_no:
                seen_bids.add(bid_no.upper())
            key = re.sub(r"\s+", "", bid_no) if bid_no else \
                hashlib.sha1(f"{abs_url}|{title}".encode()).hexdigest()[:12]
            out.append(Opportunity(
                id=f"bidpage:{page_host}:{key}",
                source_name=f"{self.name} — {agency_name}",
                title=title[:300],
                description=row_text[:1000] if row_text != title else title,
                agency=agency_name,
                state=state,
                url=abs_url,
                due_date=due,
            ))
        if not hinted:
            out.extend(self._linkless_table_rows(scopes, page_url, page_host, agency_name,
                                                 state, today, keep_past, seen_bids))
        return out

    def _linkless_table_rows(self, scopes, page_url, page_host, agency_name, state,
                             today, keep_past, seen_bids) -> list[Opportunity]:
        """Bid tables whose rows carry no link (e.g. 'download specs' elsewhere):
        keep a row only if it has a bid number AND a date; url = the page."""
        out = []
        for scope in scopes:
            for tr in scope.find_all("tr"):
                real_links = [x for x in tr.find_all("a", href=True)
                              if not x["href"].startswith(("mailto:", "tel:", "#", "javascript:"))
                              and "/cdn-cgi/" not in x["href"]]
                if real_links or not tr.find("td") or not _in_bid_table(tr):
                    continue
                row_text = clean_text(tr.get_text(" "))
                if len(row_text) > 1200 or _DEAD_RE.search(row_text):
                    continue
                bid_no = _bid_number(row_text)
                due = _due_date(_cell_by_header(tr, r"due|clos|deadline|opening|submi")) or _due_date(row_text)
                if not bid_no or not due or (due < today and not keep_past):
                    continue
                if bid_no.upper() in seen_bids:
                    continue
                named = _cell_by_header(tr, r"^(bid\s+|project\s+|solicitation\s+)?(title|name|description)\b")
                cells = [clean_text(td.get_text(" ")) for td in tr.find_all("td")]
                cells = [re.sub(r"^\W*\w*\W*" + re.escape(bid_no) + r"\W*", "", c) if bid_no in c else c
                         for c in cells]
                cells = [c for c in cells if c and not _DATE_RE.search(c)
                         and not re.fullmatch(r"[\d\s-]+", c) and len(c.split()) >= 2]
                best = named or (max(cells, key=len) if cells else "")
                if not best:
                    continue
                best = re.split(r"\b(?:\w+\s+)?(?:coordinator|contact|buyer|e-?mail)\s*:", best, flags=re.I)[0].strip(" -–—|")
                if not best:
                    continue
                seen_bids.add(bid_no.upper())
                title = f"{bid_no} — {best[:250]}"
                out.append(Opportunity(
                    id=f"bidpage:{page_host}:{bid_no.replace(' ', '')}",
                    source_name=f"{self.name} — {agency_name}",
                    title=title,
                    description=row_text[:1000],
                    agency=agency_name,
                    state=state,
                    url=page_url,
                    due_date=due,
                ))
        return out


# ---------------------------------------------------------------- helpers
def _strip_chrome(soup) -> None:
    for tag in soup.find_all(["script", "style", "noscript", "svg", "iframe"]):
        tag.decompose()
    body = soup.body or soup
    total = max(len(body.get_text(" ", strip=True)), 1)

    def big(el) -> bool:
        # Never strip a wrapper holding a large share of the page: some CMSs
        # (CivicPlus "widget", WordPress "...-menu-...") wrap the content itself.
        return len(el.get_text(" ", strip=True)) > 0.35 * total

    for tag in soup.find_all(_CHROME_TAGS):
        if not getattr(tag, "decomposed", False) and tag.parent is not None and not big(tag):
            tag.decompose()
    for el in soup.find_all(attrs={"role": re.compile(r"navigation|banner|contentinfo|menu|search", re.I)}):
        if not getattr(el, "decomposed", False) and el.parent is not None and not big(el):
            el.decompose()
    for el in list(soup.find_all(True)):
        if getattr(el, "decomposed", False) or el.parent is None:
            continue
        if el.name in ("body", "html", "main", "table", "tbody", "tr", "td", "article"):
            continue
        attrs = getattr(el, "attrs", None) or {}
        marker = " ".join(attrs.get("class", []) or []) + " " + (attrs.get("id") or "")
        if marker.strip() and _CHROME_RE.search(marker) and not big(el):
            el.decompose()


def _heading_level(el) -> int:
    if el.name and re.fullmatch(r"h[1-6]", el.name):
        return int(el.name[1])
    return 0


def _drop_dead_sections(soup) -> None:
    """Remove content under headings like 'Awarded Bids' / 'Closed Solicitations'."""
    for h in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        if h.parent is None:
            continue
        text = clean_text(h.get_text(" "))
        if not text or len(text) > 80 or not _DEAD_SECTION_RE.search(text):
            continue
        if re.search(r"\b(open|current|active)\b", text, re.I):
            continue
        level = _heading_level(h)
        sib = h.find_next_sibling()
        while sib is not None:
            nxt = sib.find_next_sibling()
            lvl = _heading_level(sib)
            if lvl and lvl <= level:
                break
            sib.decompose()
            sib = nxt
        # Accordion / tab pattern: heading is the only label inside its panel.
        parent = h.parent
        if parent is not None and parent.name in ("div", "section", "details") and \
                len(clean_text(parent.get_text(" "))) - len(text) < 3:
            panel = parent.find_next_sibling()
            if panel is not None and not _heading_level(panel):
                panel.decompose()
        h.decompose()


def _row_for(a):
    """Nearest container that represents ONE entry around this link."""
    node = a.parent
    fallback = a
    while node is not None and node.name not in ("body", "html", "[document]"):
        if node.name in _ROW_TAGS:
            if node.name != "tr":
                # A <p>/<li> inside a table cell: the table row is the entry.
                tr = node.find_parent("tr")
                if tr is not None and len(tr.get_text(" ", strip=True)) <= 1500:
                    return tr
            if len(node.get_text(" ", strip=True)) <= _MAX_ROW_CHARS or node.name == "tr":
                return node
            return fallback
        if node.name in _BLOCK_TAGS:
            if len(node.get_text(" ", strip=True)) > _MAX_ROW_CHARS:
                return fallback
            # A short div holding several separate links is a list, not a row.
            if len(node.find_all("a", href=True)) > 4:
                return fallback
            fallback = node
        node = node.parent
    return fallback


def _is_generic(text: str) -> bool:
    if _DATE_RE.search(text) and len(_DATE_RE.sub("", text).strip(" -–—,:|apmAPM0123456789")) < 4:
        return True
    return len(text) < 6 or bool(_GENERIC_TITLE_RE.match(text)) or \
        bool(re.fullmatch(r"(addend(um|a)\s*#?\s*\d*|attachment.*|exhibit.*|specs?|"
                          r"specifications|plans?|plan\s+holders?|.*\.pdf|\(?pdf\)?|"
                          r"\d+(\.\d+)?\s*(kb|mb))", text, re.I))


def _pick_title(a, row, link_text: str, row_text: str) -> str:
    if row.name == "tr" and link_text and len(link_text.split()) < 4 and _bid_number(link_text):
        # Link is just the bid number; the description lives in another cell.
        desc = _cell_desc(a, row)
        if not desc:
            cells = [clean_text(td.get_text(" ")) for td in row.find_all(["td", "th"])]
            cells = [c for c in cells if c and c != link_text and not _is_generic(c)
                     and not _DATE_RE.search(c) and len(c) < 250
                     and not re.match(r"none\b|n/?a\b", c, re.I)]
            desc = max(cells, key=len) if cells else ""
        title = f"{link_text} — {desc}" if desc else link_text
    elif link_text and len(link_text.split()) < 4 and _bid_number(link_text) and row is not a:
        # Bid-number link inside a short block: description is the rest of the block.
        other = row_text.replace(link_text, " ", 1)
        other = re.sub(r"\b(more\s+info|email|details|view|download|pdf)\b", " ", other, flags=re.I)
        other = re.sub(r"\s+", " ", other).strip(" -–—|:•")
        title = f"{link_text} — {other[:200]}" if len(other) >= 10 else link_text
    elif link_text and not _is_generic(link_text) and len(link_text) >= 12:
        title = link_text
    elif row.name == "tr":
        cells = [clean_text(td.get_text(" ")) for td in row.find_all(["td", "th"])]
        cells = [c for c in cells if c and not _DATE_RE.fullmatch(c) and not _is_generic(c)]
        title = max(cells, key=len) if cells else ""
    else:
        title = ""
        # "<h3>Title</h3><p>schedule ... [View RFP]</p>": the heading right before the row.
        prev = row.find_previous_sibling() if row is not a else None
        if prev is not None and _heading_level(prev):
            title = clean_text(prev.get_text(" "))
        elif row is not a and row_text and len(row_text) <= 200:
            title = row_text
    if not title or _is_generic(title):
        # Heading-then-links layout: use the nearest preceding heading.
        h = a.find_previous(["h2", "h3", "h4", "h5", "strong"])
        if h is not None:
            ht = clean_text(h.get_text(" "))
            if ht and not _is_generic(ht) and len(ht) <= 200:
                title = ht
    # "Request for Bids 298-IBR2102-27" says nothing about the work: use the
    # nearest heading carrying the same bid number when there is one.
    bid_no = _bid_number(title or "")
    if bid_no:
        rest = _KEYWORD_RE.sub(" ", (title or "").replace(bid_no, " "))
        rest = re.sub(r"\b(advertisement|notice|for|of|no|number|#|the)\b|[^\w]", " ", rest, flags=re.I)
        if len(rest.split()) < 2 and row.name == "tr":
            # Description is the first plain text block in the link's own cell,
            # or the row's title/description column.
            first = _cell_desc(a, row) or _cell_by_header(row, r"title|description|project|name")
            if first and len(first.split()) >= 2 and len(first) <= 200:
                title = f"{bid_no} — {first}"
        elif len(rest.split()) < 2:
            h = a.find_previous(["h2", "h3", "h4", "h5", "h6", "strong", "b"])
            ht = clean_text(h.get_text(" ")) if h is not None else ""
            if ht and len(ht) <= 200 and bid_no.split("-")[-2 if "-" in bid_no else -1] in ht:
                title = ht
    title = re.sub(r"\s*\((pdf|PDF)[^)]*\)\s*", " ", title or "")
    title = re.sub(r"^\s*(advertisement|ad|notice)\s*[:\-–]?\s+(?=\S*\d)", "", title, flags=re.I)
    title = re.sub(r"\s+", " ", title).strip(" -–—|:•")
    if len(title) < 10 or len(title.split()) < 2:
        return ""
    return title


def _stale_year(url: str, title: str) -> bool:
    """Upload paths like /uploads/2021/12/ or titles starting '20200602 ...'
    from more than a year ago mean an archived document, not a live bid."""
    this_year = date.today().year
    m = re.search(r"/(?:uploads|files|documents|media)/(20\d{2})/", url)
    if m and int(m.group(1)) < this_year - 1:
        return True
    m = re.match(r"\s*(20\d{2})[01]\d[0-3]\d\b", title)
    return bool(m and int(m.group(1)) < this_year - 1)


def _cell_by_header(tr, pattern: str) -> str:
    """Text of the cell in `tr` whose column header matches `pattern`."""
    table = tr.find_parent("table")
    if table is None:
        return ""
    head = table.find("thead") or table.find("tr")
    if head is None or head is tr:
        return ""
    heads = [clean_text(th.get_text(" ")).lower() for th in head.find_all(["th", "td"])]
    cells = tr.find_all(["td", "th"], recursive=False) or tr.find_all(["td", "th"])
    for i, h in enumerate(heads):
        if re.search(pattern, h, re.I) and i < len(cells):
            return clean_text(cells[i].get_text(" "))
    return ""


def _cell_desc(a, row) -> str:
    """First plain-text block (no link, no date) in the link's own table cell."""
    cell = a.find_parent(["td", "th"])
    if cell is None:
        return ""
    for blk in cell.find_all(["p", "div", "span", "strong"]):
        if blk.find("a") is None:
            t = clean_text(blk.get_text(" "))
            if t and not _DATE_RE.search(t) and len(t.split()) >= 2 and len(t) <= 200:
                return t
    return ""


def _in_bid_table(row) -> bool:
    table = row.find_parent("table") if row.name != "table" else row
    if table is None:
        return False
    head = table.find("thead") or table.find("tr")
    if head is None:
        return False
    return bool(_BID_TABLE_HEAD_RE.search(clean_text(head.get_text(" "))))


def _score(a, row, title: str, row_text: str, abs_url: str) -> int:
    s = 0
    if _KEYWORD_RE.search(title):
        s += 2
    elif _KEYWORD_RE.search(row_text):
        s += 1
    if _bid_number(title) or _bid_number(row_text):
        s += 2
    if row.name == "tr" and _in_bid_table(row):
        s += 2
    if _DATE_RE.search(row_text):
        s += 1
    if _DOC_RE.search(abs_url):
        s += 1
    if row.name in ("tr", "li", "article", "dd"):
        s += 1
    return s


def _bid_number(text: str) -> str:
    for m in _BIDNO_RE.finditer(text or ""):
        val = next((g for g in m.groups() if g), "")
        val = val.strip(" .:-/")
        anchored = bool(m.group(1) or m.group(2))   # "RFP 26-01", "No. 26-01", "#26-01"
        # Reject things that are really dates or phone numbers.
        if not val or _DATE_RE.fullmatch(val) or re.search(r"\d{3}[-\s.]\d{3}[-.]\d{4}", text[max(0, m.start() - 4):m.end() + 1]):
            continue
        if not anchored and re.fullmatch(r"\d{3}-\d{4}", val):
            continue
        if not re.search(r"\d", val) or len(val) < 3:
            continue
        return val
    return ""


def _context_text(row) -> str:
    """Text of the few siblings just before the row (stopping at a heading) —
    covers 'heading / description / Due: date / [links]' layouts."""
    parts, n = [], 0
    sib = row.find_previous_sibling() if hasattr(row, "find_previous_sibling") else None
    while sib is not None and n < 4:
        t = clean_text(sib.get_text(" "))
        if len(t) > 1200:
            break
        parts.insert(0, t)
        if _heading_level(sib) or sib.name == "hr":
            break
        sib = sib.find_previous_sibling()
        n += 1
    return " ".join(parts)


def _due_date(text: str, cue_only: bool = False) -> str:
    """Prefer a date right after a due/closing cue; else the latest date in the text."""
    strong, weak, latest = "", "", ""
    for m in _DATE_RE.finditer(text or ""):
        iso = to_iso_date(m.group(0))
        if not iso:
            continue
        try:
            date.fromisoformat(iso)
        except ValueError:
            continue
        before = text[max(0, m.start() - 40):m.start()]
        after = text[m.end():m.end() + 30]
        if not strong and (_STRONG_CUE_BEFORE_RE.search(before) or _STRONG_CUE_AFTER_RE.match(after)):
            strong = iso
        if not weak and _DUE_CUE_RE.search(before) and not re.search(r"question|inquir|pre-?bid|post", before, re.I):
            weak = iso
        latest = max(latest, iso)
    best = strong or weak
    return best if cue_only else (best or latest)
