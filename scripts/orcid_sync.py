#!/usr/bin/env python3
"""
Keep the Publications section of the website in sync with ORCID.

Pipeline
  1. ORCID public API (pub.orcid.org/v3.0)  ->  data/orcid.json   (cache)
  2. Crossref, by DOI, fills authors/venue/event when ORCID lacks them
  3. data/publications.extra.json adds what ORCID cannot know
     (code links, awards, short venue names) and provides fallback
     entries for papers that are not on ORCID yet
  4. Writes data/publications.json, publications.bib and the block
     between <!-- ORCID:BEGIN --> and <!-- ORCID:END --> in index.html

Usage
  python scripts/orcid_sync.py             fetch from ORCID, then render
  python scripts/orcid_sync.py --offline   re-render from the cache only

Files are rewritten only when their content changes, so a daily run
produces a commit only when something actually changed. Only the Python
standard library is used.
"""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import html
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXTRA_PATH = ROOT / "data" / "publications.extra.json"
CACHE_PATH = ROOT / "data" / "orcid.json"
OUT_JSON = ROOT / "data" / "publications.json"
OUT_BIB = ROOT / "publications.bib"
INDEX = ROOT / "index.html"

BEGIN = "<!-- ORCID:BEGIN -->"
END = "<!-- ORCID:END -->"

NEW_BADGE_DAYS = 90
TITLE_MATCH = 0.88

SERIES_RE = re.compile(
    r"^(lecture notes|communications in computer|advances in intelligent|"
    r"studies in computational|smart innovation|lncs|ccis|ifip)", re.I)
CONF_RE = re.compile(
    r"(lecture notes|proceedings|conference|symposium|workshop|congress|"
    r"discovery science|similarity search)", re.I)
PARTICLES = {"de", "di", "da", "del", "della", "dei", "degli", "dal", "dalla",
             "van", "von", "der", "den", "ter", "le", "la", "lo", "du", "dos",
             "das", "do", "st", "st.", "bin", "al", "el"}

KIND_LABEL = {"journal": "Journal", "conference": "Conference",
              "preprint": "Preprint", "thesis": "Thesis", "other": "Other"}
KIND_PLURAL = {"journal": "Journals", "conference": "Conferences",
               "preprint": "Preprints", "thesis": "Theses", "other": "Other"}
KIND_ORDER = ["journal", "conference", "preprint", "thesis", "other"]


# --------------------------------------------------------------- utilities

def log(msg: str) -> None:
    print(msg, flush=True)


def warn(msg: str) -> None:
    # Rendered as a yellow annotation in the GitHub Actions run summary.
    prefix = "::warning::" if os.environ.get("GITHUB_ACTIONS") else "warning: "
    print(prefix + msg, flush=True)


def dig(obj, *path):
    for key in path:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def as_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def norm_title(text: str | None) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def norm_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    doi = doi.strip()
    doi = re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", doi, flags=re.I)
    doi = re.sub(r"^doi:\s*", "", doi, flags=re.I)
    return doi.lower() or None


def slugify(text: str, limit: int = 48) -> str:
    text = norm_title(text).replace(" ", "-")
    return text[:limit].rstrip("-") or "item"


def write_if_changed(path: Path, content: str) -> bool:
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    return True


def dump_json(data) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def http_json(url: str, headers: dict | None = None, retries: int = 3,
              user_agent: str = "") -> dict:
    hdrs = {"Accept": "application/json",
            "User-Agent": user_agent or "personal-website-orcid-sync/1.0"}
    hdrs.update(headers or {})
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as err:
            retryable = err.code in (429, 500, 502, 503, 504)
            if not retryable or attempt == retries - 1:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == retries - 1:
                raise
        time.sleep(2 * 2 ** attempt)
    raise RuntimeError("unreachable")


# ------------------------------------------------------------------ names

def split_name(name: str) -> tuple[str, str]:
    """Return (given, family) for 'Given Family' or 'Family, Given'."""
    name = re.sub(r"[{}]", "", name or "").strip()
    name = re.sub(r"\s+", " ", name)
    if not name:
        return "", ""
    if "," in name:
        family, given = [p.strip() for p in name.split(",", 1)]
        return given, family
    tokens = name.split(" ")
    if len(tokens) == 1:
        return "", tokens[0]
    i = len(tokens) - 1
    while i > 1 and tokens[i - 1].lower() in PARTICLES:
        i -= 1
    return " ".join(tokens[:i]), " ".join(tokens[i:])


def initials(given: str) -> str:
    parts = []
    for word in given.replace(".", ". ").split():
        if "-" in word.strip("."):
            parts.append("-".join(p[0] + "." for p in word.split("-") if p))
        elif word:
            parts.append(word[0] + ".")
    return " ".join(parts)


def short_name(name: str) -> str:
    given, family = split_name(name)
    return f"{initials(given)} {family}".strip() if given else family


def bib_name(name: str) -> str:
    given, family = split_name(name)
    return f"{family}, {given}" if given else family


def name_key(name: str) -> str:
    return norm_title(short_name(name)).replace(" ", "")


# ----------------------------------------------------------------- bibtex

def bib_field(bibtex: str, field: str) -> str | None:
    match = re.search(rf"\b{field}\s*=\s*", bibtex, re.I)
    if not match:
        return None
    i = match.end()
    if i >= len(bibtex):
        return None
    if bibtex[i] == "{":
        depth, j = 0, i
        while j < len(bibtex):
            if bibtex[j] == "{":
                depth += 1
            elif bibtex[j] == "}":
                depth -= 1
                if depth == 0:
                    return bibtex[i + 1:j]
            j += 1
        return None
    if bibtex[i] == '"':
        j = bibtex.find('"', i + 1)
        return bibtex[i + 1:j] if j > i else None
    value = re.match(r"[^,}\n]+", bibtex[i:])
    return value.group(0).strip() if value else None


def bib_escape(text: str) -> str:
    return (text or "").replace("&", r"\&").replace("%", r"\%")


def make_bibtex(p: dict) -> str:
    entry = {"journal": "article", "conference": "inproceedings"}.get(
        p.get("kind"), "misc")
    first = split_name(p["authors_full"][0])[1] if p.get("authors_full") else "anon"
    words = [w for w in norm_title(p["title"]).split()
             if w not in {"a", "an", "the", "on", "of", "for", "to", "and"}]
    key = f"{slugify(first).replace('-', '')}{p.get('year') or ''}{words[0] if words else ''}"
    fields = [("title", "{" + bib_escape(p["title"]) + "}"),
              ("author", " and ".join(bib_name(a) for a in p.get("authors_full", [])))]
    venue = p.get("venue")
    if entry == "article":
        fields.append(("journal", venue))
    elif entry == "inproceedings":
        fields.append(("booktitle", venue))
        fields.append(("series", p.get("series")))
    else:
        fields.append(("howpublished", venue))
    fields += [("volume", p.get("volume")), ("number", p.get("issue")),
               ("pages", re.sub(r"\s*[-–—]+\s*", "--", str(p.get("pages") or "")) or None),
               ("year", p.get("year")), ("publisher", p.get("publisher")),
               ("doi", p.get("doi")),
               ("url", None if p.get("doi") else p.get("url"))]
    body = ",\n".join(f"  {k} = {{{bib_escape(str(v))}}}" if k != "title"
                      else f"  {k} = {{{v}}}"
                      for k, v in fields if v)
    return f"@{entry}{{{key},\n{body}\n}}"


# ------------------------------------------------------------------ ORCID

def orcid_work_to_record(w: dict) -> dict:
    ext = dig(w, "external-ids", "external-id") or []
    doi, ids = None, {}
    for kind_pref in ("self", None):
        for e in ext:
            etype = (e.get("external-id-type") or "").lower()
            rel = (e.get("external-id-relationship") or "").lower()
            value = e.get("external-id-value")
            if etype == "doi" and value and not doi:
                if (kind_pref and rel == kind_pref) or (not kind_pref and rel != "part-of"):
                    doi = norm_doi(value)
    for e in ext:
        etype = (e.get("external-id-type") or "").lower()
        if etype and etype != "doi" and e.get("external-id-value"):
            ids.setdefault(etype, e.get("external-id-value"))

    authors = []
    for c in dig(w, "contributors", "contributor") or []:
        role = (dig(c, "contributor-attributes", "contributor-role") or "").lower()
        name = dig(c, "credit-name", "value")
        if name and "editor" not in role and "translator" not in role:
            authors.append(name.strip())

    bibtex = None
    if (dig(w, "citation", "citation-type") or "").lower() == "bibtex":
        bibtex = (dig(w, "citation", "citation-value") or "").strip() or None
    if not authors and bibtex:
        raw = bib_field(bibtex, "author")
        if raw:
            authors = [a.strip() for a in re.split(r"\s+and\s+", raw) if a.strip()]

    date = w.get("publication-date") or {}
    title = (dig(w, "title", "title", "value") or "").strip()
    subtitle = (dig(w, "title", "subtitle", "value") or "").strip()
    if subtitle and subtitle.lower() not in title.lower():
        title = f"{title}: {subtitle}"

    return {
        "put_code": w.get("put-code"),
        "title": title,
        "authors": authors,
        "venue": (dig(w, "journal-title", "value") or "").strip() or None,
        "type": (w.get("type") or "").lower() or None,
        "year": as_int(dig(date, "year", "value")),
        "month": as_int(dig(date, "month", "value")),
        "day": as_int(dig(date, "day", "value")),
        "doi": doi,
        "url": dig(w, "url", "value"),
        "ids": ids,
        "bibtex": bibtex,
    }


def fetch_orcid(orcid: str, token: str | None, ua: str) -> list[dict]:
    base = f"https://pub.orcid.org/v3.0/{orcid}"
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    summary = http_json(f"{base}/works", headers, user_agent=ua)

    picks = []
    for group in summary.get("group") or []:
        versions = group.get("work-summary") or []
        if versions:
            # The version the owner marked as preferred has the highest index.
            picks.append(max(versions, key=lambda s: as_int(s.get("display-index")) or 0))

    full = {}
    codes = [str(s.get("put-code")) for s in picks]
    for i in range(0, len(codes), 100):
        chunk = ",".join(codes[i:i + 100])
        try:
            bulk = http_json(f"{base}/works/{chunk}", headers, user_agent=ua)
            for item in bulk.get("bulk") or []:
                work = item.get("work")
                if work:
                    full[str(work.get("put-code"))] = work
        except Exception as err:  # summaries still have title/date/DOI
            warn(f"ORCID bulk read failed, using summaries only: {err}")

    return [orcid_work_to_record(full.get(str(s.get("put-code"))) or s) for s in picks]


# --------------------------------------------------------------- Crossref

def enrich_with_crossref(rec: dict, ua: str) -> None:
    venue = rec.get("venue") or ""
    needs = (not rec["authors"] or not venue or SERIES_RE.search(venue)
             or rec.get("type") in ("book-chapter", "conference-paper", None))
    if not rec.get("doi") or not needs:
        return
    try:
        msg = http_json("https://api.crossref.org/works/" +
                        urllib.parse.quote(rec["doi"]), user_agent=ua)["message"]
    except Exception as err:
        warn(f"Crossref lookup failed for {rec['doi']}: {err}")
        return

    if not rec["authors"]:
        names = []
        for a in msg.get("author") or []:
            full = " ".join(x for x in (a.get("given"), a.get("family")) if x)
            names.append(full or a.get("name") or "")
        rec["authors"] = [n for n in names if n]

    containers = [c for c in msg.get("container-title") or [] if c]
    proper = [c for c in containers if not SERIES_RE.search(c)]
    series = [c for c in containers if SERIES_RE.search(c)]
    if (not venue or SERIES_RE.search(venue)) and (proper or containers):
        if venue and SERIES_RE.search(venue):
            rec["series"] = venue
        rec["venue"] = (proper or containers)[-1]
    if series and not rec.get("series"):
        rec["series"] = series[0]

    event = msg.get("event") or {}
    acronym = (event.get("acronym") or "").strip()
    event_year = as_int((dig(event, "start", "date-parts") or [[None]])[0][0])
    if acronym:
        rec["short"] = f"{acronym} {event_year or rec.get('year') or ''}".strip()

    rec["crossref_type"] = msg.get("type")
    rec["volume"] = rec.get("volume") or msg.get("volume")
    rec["issue"] = rec.get("issue") or msg.get("issue")
    rec["pages"] = rec.get("pages") or msg.get("page") or msg.get("article-number")
    rec["publisher"] = rec.get("publisher") or msg.get("publisher")
    if not rec.get("year"):
        parts = dig(msg, "issued", "date-parts") or [[None]]
        rec["year"] = as_int(parts[0][0]) if parts and parts[0] else None


# ------------------------------------------------------------------ merge

def kind_of(rec: dict) -> str:
    t = (rec.get("type") or "").lower()
    ct = (rec.get("crossref_type") or "").lower()
    context = " ".join(filter(None, [rec.get("venue"), rec.get("series")]))
    if t in ("journal-article", "review", "journal-issue") or ct == "journal-article":
        return "journal"
    if t.startswith("conference") or ct in ("proceedings-article", "proceedings"):
        return "conference"
    if t == "book-chapter" and (CONF_RE.search(context) or rec.get("short")):
        return "conference"
    if t == "preprint" or ct == "posted-content":
        return "preprint"
    if t.startswith("dissertation"):
        return "thesis"
    return "other"


def same_work(a: dict, b: dict) -> float:
    if a.get("doi") and b.get("doi"):
        return 1.0 if norm_doi(a["doi"]) == norm_doi(b["doi"]) else 0.0
    ta, tb = norm_title(a.get("title")), norm_title(b.get("title"))
    if not ta or not tb:
        return 0.0
    score = difflib.SequenceMatcher(None, ta, tb).ratio()
    # "LLiMe: ..." and "LLiMe: ..." of the same year are the same paper even
    # if the subtitles were typed differently.
    na = norm_title((a.get("title") or "").split(":")[0]) if ":" in (a.get("title") or "") else ""
    nb = norm_title((b.get("title") or "").split(":")[0]) if ":" in (b.get("title") or "") else ""
    ya, yb = as_int(a.get("year")), as_int(b.get("year"))
    if na and na == nb and len(na) >= 4 and (not ya or not yb or abs(ya - yb) <= 1):
        score = max(score, 0.95)
    return score


def dedupe(works: list[dict]) -> list[dict]:
    kept: list[dict] = []
    for w in works:
        dup = next((k for k in kept if same_work(k, w) >= 0.97), None)
        if dup is None:
            kept.append(w)
        elif len([v for v in w.values() if v]) > len([v for v in dup.values() if v]):
            kept[kept.index(dup)] = w
    return kept


ANNOTATIONS = ("id", "code", "award", "short", "pdf", "slides", "video",
               "poster", "kind")
FALLBACKS = ("authors", "venue", "year", "month", "doi", "url", "volume",
             "issue", "pages", "publisher", "series", "type", "bibtex")


def finalise(rec: dict, self_names: set[str]) -> dict:
    rec["authors_full"] = list(rec.get("authors") or [])
    rec["authors"] = [short_name(a) for a in rec["authors_full"]]
    rec["self"] = [i for i, a in enumerate(rec["authors_full"])
                   if name_key(a) in self_names]
    rec["kind"] = rec.get("kind") or kind_of(rec)
    rec["doi"] = norm_doi(rec.get("doi"))
    rec["link"] = f"https://doi.org/{rec['doi']}" if rec["doi"] else rec.get("url")
    rec["id"] = rec.get("id") or slugify(rec["title"], 40)
    # Keep a BibTeX entry from ORCID only if it is complete enough.
    bib = rec.get("bibtex") or ""
    if not (bib_field(bib, "author") and bib_field(bib, "title") and bib_field(bib, "year")
            and (bib_field(bib, "journal") or bib_field(bib, "booktitle"))):
        rec["bibtex"] = make_bibtex(rec)
    return rec


def merge(works: list[dict], extra: dict) -> list[dict]:
    papers = extra.get("papers") or []
    self_names = {name_key(n) for n in extra.get("self_names") or []}
    used: set[int] = set()
    out: list[dict] = []

    for w in works:
        best, best_score = None, TITLE_MATCH
        for i, p in enumerate(papers):
            if i in used:
                continue
            score = same_work(w, p)
            if score >= best_score:
                best, best_score = i, score
        rec = dict(w)
        rec["source"] = "orcid"
        if best is not None:
            used.add(best)
            p = papers[best]
            if p.get("hide"):
                continue
            for k in ANNOTATIONS:
                if p.get(k):
                    rec[k] = p[k]
            for k in FALLBACKS:
                if not rec.get(k) and p.get(k):
                    rec[k] = p[k]
            rec.update(p.get("force") or {})
        out.append(finalise(rec, self_names))

    for i, p in enumerate(papers):
        if i in used or p.get("hide"):
            continue
        if not all(p.get(k) for k in ("title", "authors", "venue", "year")):
            warn(f"'{p.get('title')}' is not on ORCID and lacks title/authors/"
                 "venue/year in publications.extra.json, so it is not shown")
            continue
        rec = {k: v for k, v in p.items() if k not in ("force", "hide")}
        rec.update(p.get("force") or {})
        rec["source"] = "manual"
        out.append(finalise(rec, self_names))

    # Newest first; papers without a month keep their relative order.
    out.sort(key=lambda r: (r.get("year") or 0, r.get("month") or 0,
                            r.get("day") or 0), reverse=True)
    seen: dict[str, int] = {}
    for r in out:  # make anchors unique
        n = seen.get(r["id"], 0)
        seen[r["id"]] = n + 1
        if n:
            r["id"] = f"{r['id']}-{n + 1}"
    return out


# ----------------------------------------------------------------- render

def esc(text) -> str:
    return html.escape(str(text), quote=True)


def fmt_date(iso: str | None) -> str:
    if not iso:
        return ""
    d = dt.date.fromisoformat(iso[:10])
    return f"{d.day} {d.strftime('%B')} {d.year}"


def render_pub(p: dict, today: dt.date) -> str:
    authors = []
    for i, name in enumerate(p["authors"]):
        authors.append(f'<span class="me">{esc(name)}</span>' if i in p["self"]
                       else esc(name))
    title = esc(p["title"])
    if p.get("link"):
        title = f'<a href="{esc(p["link"])}">{title}</a>'

    venue = f'<span class="pub-venue-name">{esc(p["venue"])}</span>' if p.get("venue") else ""
    if p.get("short") and norm_title(p["short"]) not in norm_title(p.get("venue")):
        venue += f" ({esc(p['short'])})"
    if p.get("kind") == "journal" and p.get("volume"):
        venue += f" {esc(p['volume'])}"
    year = f", {p['year']}" if p.get("year") else ""

    new = ""
    seen = p.get("first_seen")
    if seen and seen != "initial":
        if (today - dt.date.fromisoformat(seen[:10])).days <= NEW_BADGE_DAYS:
            new = '<span class="pub-new">New</span>'

    lines = [
        f'<li class="pub" id="pub-{esc(p["id"])}" data-kind="{esc(p["kind"])}">',
        f'  <p class="pub-title">{title}{new}</p>',
        f'  <p class="pub-authors">{", ".join(authors)}</p>',
        f'  <p class="pub-venue">{venue}{year}'
        f' <span class="pub-kind">{KIND_LABEL.get(p["kind"], "Other")}</span></p>',
    ]
    if p.get("award"):
        lines.append(f'  <p class="pub-award">{esc(p["award"])}</p>')
    links = []
    if p.get("link"):  # doi.org resolves to the publisher's page
        links.append(f'<a href="{esc(p["link"])}">Paper</a>')
    for key, label in (("pdf", "PDF"), ("code", "Code"), ("slides", "Slides"),
                       ("poster", "Poster"), ("video", "Video")):
        if p.get(key):
            links.append(f'<a href="{esc(p[key])}">{label}</a>')
    if links:
        lines.append(f'  <p class="pub-links">{" ".join(links)}</p>')
    lines.append("</li>")
    return "\n".join(lines)


def render_block(pubs: list[dict], orcid: str, changed: str | None,
                 today: dt.date) -> str:
    counts = {k: sum(1 for p in pubs if p["kind"] == k) for k in KIND_ORDER}
    buttons = [f'<button type="button" data-filter="all" aria-pressed="true">'
               f'All <span>{len(pubs)}</span></button>']
    for k in KIND_ORDER:
        if counts[k]:
            buttons.append(f'<button type="button" data-filter="{k}" aria-pressed="false">'
                           f'{KIND_PLURAL[k]} <span>{counts[k]}</span></button>')

    parts = [BEGIN,
             '<div class="pubs">',
             '<div class="pub-filter" role="group" aria-label="Show publications" hidden>',
             "\n".join(buttons),
             "</div>"]
    years: list[int | None] = []
    for p in pubs:
        if p.get("year") not in years:
            years.append(p.get("year"))
    for y in years:
        label = str(y) if y else "Undated"
        items = "\n".join(render_pub(p, today) for p in pubs if p.get("year") == y)
        parts.append(f'<section class="pub-year" aria-label="{label}">\n'
                     f'<h3>{label}</h3>\n<ol class="pub-list">\n{items}\n</ol>\n</section>')
    note = '<a href="publications.bib" download>Download all as BibTeX</a>.'
    if any(p["source"] == "orcid" for p in pubs):
        note = (f'Updated from <a href="https://orcid.org/{esc(orcid)}">ORCID</a>'
                + (f" on {fmt_date(changed)}" if changed else "") + ". " + note)
    parts += [f'<p class="pubs-note">{note}</p>', "</div>", END]
    return "\n".join(parts)


def render_bib(pubs: list[dict], orcid: str) -> str:
    head = (f"% Publications of the owner of ORCID {orcid}\n"
            "% Generated by scripts/orcid_sync.py. Do not edit by hand.\n\n")
    return head + "\n\n".join(p["bibtex"].strip() for p in pubs) + "\n"


# ------------------------------------------------------------------- main

def carry_first_seen(works: list[dict], previous: list[dict], first_run: bool) -> None:
    today = dt.date.today().isoformat()
    for w in works:
        old = next((o for o in previous if o.get("put_code") == w.get("put_code")), None)
        if old is None:
            old = next((o for o in previous if same_work(o, w) >= 0.97), None)
        if old and old.get("first_seen"):
            w["first_seen"] = old["first_seen"]
        else:
            w["first_seen"] = "initial" if first_run else today


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--offline", action="store_true",
                    help="do not contact ORCID/Crossref, re-render from cache")
    args = ap.parse_args()

    extra = json.loads(EXTRA_PATH.read_text(encoding="utf-8"))
    orcid = os.environ.get("ORCID_ID") or extra["orcid"]
    ua = ("personal-website-orcid-sync/1.0"
          + (f" (mailto:{extra['contact_email']})" if extra.get("contact_email") else ""))

    cache = {}
    if CACHE_PATH.exists():
        cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    previous = cache.get("works") or []

    works = previous
    if not args.offline:
        try:
            fetched = fetch_orcid(orcid, os.environ.get("ORCID_TOKEN"), ua)
            for rec in fetched:
                enrich_with_crossref(rec, ua)
            fetched = dedupe([w for w in fetched if w.get("title")])
            if not fetched and previous:
                warn("ORCID returned no public works; keeping the cached list. "
                     "Check that the works on your record are set to 'Everyone'.")
            else:
                carry_first_seen(fetched, previous, first_run=not previous)
                works = fetched
                log(f"ORCID: {len(works)} public works")
        except Exception as err:
            warn(f"Could not read ORCID ({err}); rendering from the cache.")

    def comparable(ws):
        return json.dumps(sorted(ws, key=lambda w: str(w.get("put_code"))),
                          sort_keys=True, ensure_ascii=False)

    changed_at = cache.get("last_change")
    if comparable(works) != comparable(previous) or not CACHE_PATH.exists():
        changed_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
        write_if_changed(CACHE_PATH, dump_json({"orcid": orcid,
                                                "last_change": changed_at,
                                                "works": works}))
        log("data/orcid.json updated")

    pubs = merge(works, extra)
    today = dt.date.today()

    public = [{k: v for k, v in p.items() if k not in ("authors_full",)} for p in pubs]
    touched = []
    if write_if_changed(OUT_JSON, dump_json({"orcid": orcid, "last_change": changed_at,
                                             "publications": public})):
        touched.append(OUT_JSON.name)
    if write_if_changed(OUT_BIB, render_bib(pubs, orcid)):
        touched.append(OUT_BIB.name)

    page = INDEX.read_text(encoding="utf-8")
    if BEGIN not in page or END not in page:
        warn(f"{INDEX.name} has no {BEGIN} ... {END} markers; page not updated")
    else:
        head, rest = page.split(BEGIN, 1)
        _, tail = rest.split(END, 1)
        block = render_block(pubs, orcid, changed_at, today)
        if write_if_changed(INDEX, head + block + tail):
            touched.append(INDEX.name)

    manual = sum(1 for p in pubs if p["source"] == "manual")
    log(f"{len(pubs)} publications rendered ({len(pubs) - manual} from ORCID, "
        f"{manual} from publications.extra.json)")
    log("changed: " + (", ".join(touched) if touched else "nothing"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
