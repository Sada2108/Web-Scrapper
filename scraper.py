"""
scraper.py
----------
Core engine that turns a natural-language PCB/circuit design prompt into a
research corpus (text, schematics, images, source links) using Firecrawl.

Pipeline:
  1. prompt  -> generate_search_queries()   (keyword/topic extraction)
  2. queries -> search_sources()            (Firecrawl /search)
  3. sources -> scrape_source()             (Firecrawl /scrape, markdown+html)
  4. html    -> extract_images()            (filter for schematic/circuit imgs)
  5. everything -> run_pipeline()           (orchestrates + caches to JSON)

Requires: pip install firecrawl-py python-dotenv
Env var:  FIRECRAWL_API_KEY  (or pass api_key= explicitly)
"""

from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()

import html as _html
import json
import os
import re
import sys
import time
import hashlib
import requests
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Dict, Optional
from urllib.parse import urlparse, urljoin

from firecrawl import Firecrawl
try:
    from firecrawl.types import ClickAction, WaitAction, ExecuteJavascriptAction
except ImportError:
    ClickAction = WaitAction = ExecuteJavascriptAction = None

try:
    from pdf_images import extract_pdf_figures, save_figures, extract_pdf_tables
    _HAS_PDF = True
except ImportError:
    _HAS_PDF = False

try:
    from bs4 import BeautifulSoup
    _HAS_BS4 = True
except ImportError:
    _HAS_BS4 = False

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

_MODULE_DIR = Path(__file__).parent.resolve()
CACHE_DIR = Path(os.environ.get("PCB_SCRAPER_CACHE", str(_MODULE_DIR / "cache"))).resolve()
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# xAI Grok API (OpenAI-compatible chat completions endpoint). Used for the
# optional "AI Summary" pass over the scraped corpus -- everything else in
# this file works with zero LLM calls, this is purely additive.
GROK_API_BASE = "https://api.x.ai/v1"
GROK_MODEL = os.environ.get("GROK_MODEL", "grok-4.3")

# Words/phrases that make a query more likely to return schematic-rich,
# electronics-engineering sources rather than generic marketing pages.
SOURCE_HINT_SUFFIXES = [
    "application note",
    "datasheet",
    "reference design",
    "schematic",
    "circuit design",
    "pinout",
    "typical application circuit",
    "components needed",
    "how to use",
    "circuit diagram",
]

# Additional suffix pool targeting information-dense content: actual
# component values, equations, biasing methods, and specs rather than
# "what is this chip" overviews.  Merged with SOURCE_HINT_SUFFIXES below.
SCHEMA_HINT_SUFFIXES = [
    "gain resistor values",
    "single supply biasing",
    "output coupling capacitor value",
    "decoupling capacitor placement",
    "bandwidth frequency response",
    "component values",
    "bill of materials",
    "PCB layout guidelines",
]

ALL_HINT_SUFFIXES = SOURCE_HINT_SUFFIXES + SCHEMA_HINT_SUFFIXES

# When both a part number and a topic pattern match are found, this fraction
# of queries uses the compound term (e.g. "LM386 audio amplifier"); the
# remainder uses bare topic-pattern terms only as a breadth fallback.
_COMPOUND_QUOTA_FRAC = 0.75

# Trusted-ish EE domains we bias toward when present in results (not a hard
# filter -- just used for ranking).
PREFERRED_DOMAINS = [
    "ti.com", "analog.com", "onsemi.com", "microchip.com", "st.com",
    "renesas.com", "maximintegrated.com", "nxp.com", "allaboutcircuits.com",
    "electronics-tutorials.ws", "eevblog.com", "electronicdesign.com",
    "circuitdigest.com", "edn.com",
]

# Low-quality / content-marketing domains that tend to produce fluffy,
# non-technical results. These are down-ranked (not hard-excluded) so they
# still appear if they're the only results, but preferred/manufacturer sources
# always sort above them.
DEPRIORITIZED_DOMAINS = [
    "wikihow.com", "instructables.com", "hackster.io",
    "maker.pro", "electroschematics.com", "homemade-circuits.com",
    "circuitbasics.com", "randomnerdtutorials.com",
    "projecthub.arduino.cc", "create.arduino.cc", "medium.com", "dev.to",
    "hashnode.dev", "blogspot.com", "wordpress.com", "quora.com",
    "stackoverflow.com", "electronics.stackexchange.com",
]

# E-commerce / shopping domains that never contain useful technical content.
# These are hard-excluded (filtered out before ranking).
EXCLUDED_DOMAINS = [
    "amazon.com", "amazon.co.uk", "amazon.de", "amazon.fr", "amazon.ca",
    "ebay.com", "ebay.co.uk", "ebay.de", "ebay.fr",
    "aliexpress.com", "aliexpress.us",
    "alibaba.com",
]

# A lightweight EE vocabulary used to pull technical terms out of the prompt
# when we don't want to (or can't) call an LLM to do query planning.
EE_KEYWORD_PATTERNS = [
    r"chopper[- ]stabilized amplifier", r"auto[- ]zero amplifier",
    r"electrometer[- ]grade amplifier", r"transimpedance amplifier",
    r"nanovoltmeter", r"picoammeter", r"femtoammeter",
    r"Kelvin (?:input )?connection", r"guard(?:ing)? (?:ring|technique)",
    r"EMI filtering", r"auto[- ]ranging", r"feedback resistor",
    r"offset drift", r"input bias current", r"low[- ]IQ regulator",
    r"Li-?ion battery", r"op[- ]amp", r"instrumentation amplifier",
    r"ADC", r"DAC", r"low noise amplifier", r"shunt resistor",
    r"current sense", r"voltage reference", r"PCB layout", r"ground plane",
    # Generic circuit types — common request shapes that should hit a real
    # pattern before ever reaching the naive word-split fallback.
    r"audio amplifier", r"power supply", r"voltage regulator",
    r"power amplifier", r"oscillator", r"filter circuit",
    r"LED driver", r"motor driver", r"battery charger",
    r"class [A-D] amplifier", r"preamp(?:lifier)?",
    r"audio circuit", r"amplifier circuit",
]

# Generic part-number regex — matches IC part numbers like LM386, TL071,
# OPA2340, MAX232, NE555 etc.  Case-insensitive so "lm386" matches as
# well as "LM386".  When normalizing, the canonical form is UPPERCASE
# (datasheets/search engines use uppercase part numbers).
# Allows optional space or hyphen between prefix and digits so "LM 386"
# matches alongside "LM386" and "LM-386".  The word-blocklist below
# prevents common English phrases like "below 100" from being misread
# as part numbers.
_PART_NUMBER_RE = re.compile(r"\b([A-Z]{2,5})[\s\-]?(\d{2,5}[A-Z]?)\b", re.IGNORECASE)

# Common English words that are 2-5 letters and could still precede a
# number with no space in ordinary prose.  The blocklist is checked in
# _extract_context_terms() so "below 100 nV" never becomes "BELOW100".
_PART_NUMBER_WORD_BLOCKLIST = {
    "below", "above", "over", "under", "less", "more", "than", "about",
    "up", "to", "at", "in", "on", "of", "top", "type", "class", "grade",
    "gain", "rev", "version", "no", "num", "page", "step", "part",
}


def _extract_context_terms(prompt: str) -> List[str]:
    """
    Extract the highest-priority technical terms from a prompt for use as
    relevance signals downstream. Returns a list containing:
      1. Part numbers (normalized, e.g. "LM386") — strongest signal
      2. Matched EE_KEYWORD_PATTERNS terms (e.g. "audio amplifier") — strong signal
    These are used to boost scoring in _block_text_score and _score_image.
    """
    terms = []
    # Part numbers (case-insensitive now; normalize to UPPERCASE so the
    # canonical form is always "LM386", never "lm386" or "Lm386").
    for m in _PART_NUMBER_RE.finditer(prompt):
        prefix = m.group(1).lower()
        if prefix in _PART_NUMBER_WORD_BLOCKLIST:
            continue
        normalized = (m.group(1) + m.group(2)).upper()
        if normalized not in terms:
            terms.append(normalized)
    # EE keyword pattern matches (case-insensitive, from lowercased prompt)
    lower_prompt = prompt.lower()
    for pattern in EE_KEYWORD_PATTERNS:
        m = re.search(pattern, lower_prompt, flags=re.IGNORECASE)
        if m:
            term = m.group(0).strip()
            if term.lower() not in [t.lower() for t in terms]:
                terms.append(term)
    return terms


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------

@dataclass
class ScrapedImage:
    url: str
    alt: str = ""
    relevance_score: int = 0


@dataclass
class Source:
    url: str
    title: str = ""
    query: str = ""
    markdown: str = ""
    images: List[ScrapedImage] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self):
        d = asdict(self)
        return d


@dataclass
class ResearchCorpus:
    prompt: str
    queries: List[str]
    sources: List[Source]
    generated_at: float = field(default_factory=time.time)
    summary: Optional[str] = None
    summary_error: Optional[str] = None
    search_errors: List[str] = field(default_factory=list)

    def to_dict(self):
        return {
            "prompt": self.prompt,
            "queries": self.queries,
            "generated_at": self.generated_at,
            "summary": self.summary,
            "summary_error": self.summary_error,
            "search_errors": self.search_errors,
            "sources": [s.to_dict() for s in self.sources],
        }


# --------------------------------------------------------------------------
# Step 1: prompt -> search queries
# --------------------------------------------------------------------------

def generate_search_queries(prompt: str, max_queries: int = 6) -> List[str]:
    """
    Extract technical sub-topics from a free-text design prompt and turn
    them into targeted search queries. This is a heuristic keyword matcher
    (no external LLM call required); swap in an LLM-based planner later if
    you want richer query expansion.
    """
    # --- 1. Extract context terms (part numbers + EE patterns) ---
    # Uses the shared _extract_context_terms() so there's exactly ONE
    # part-number and EE-keyword extraction implementation in this file.
    context_terms = _extract_context_terms(prompt)
    part_numbers = [t for t in context_terms if any(c.isdigit() for c in t)]
    pattern_matches = [t for t in context_terms if not any(c.isdigit() for c in t)]

    # --- 2. Fallback: naive word-split when neither pattern matched ---
    # Allow alphanumeric characters so part numbers survive the split
    # (the old regex `[A-Za-z][A-Za-z\-]{3,}` silently dropped anything
    # containing digits like "LM386").  Apply STOPWORDS so filler words
    # like "using", "with", "from" never become search queries.
    fallback_words = []
    if not part_numbers and not pattern_matches:
        words = re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", prompt)
        fallback_words = [
            w for w in dict.fromkeys(words)
            if w.lower() not in STOPWORDS
        ]

    # --- 3. Merge: part numbers first, then pattern matches, then fallback ---
    found = part_numbers + pattern_matches + fallback_words
    seen = set()
    unique = []
    for term in found:
        key = term.lower()
        if key not in seen:
            seen.add(key)
            unique.append(term)
    found = unique

    # --- 5. Build queries -------------------------------------------------
    # When both a part number and a topic pattern match exist, build a
    # compound term (e.g. "LM386 audio amplifier") and use it for the
    # majority of queries.  Reserve the remainder for bare pattern-match
    # queries as a breadth fallback (a source may describe the part
    # generically without literally saying "LM386").  Bare part-number-only
    # queries are dropped when a compound is available — they're too
    # ambiguous on their own.
    n_suffixes = len(ALL_HINT_SUFFIXES)
    if max_queries <= n_suffixes:
        step = n_suffixes / max_queries
        suffix_indices = [int(i * step) for i in range(max_queries)]
    else:
        suffix_indices = list(range(n_suffixes))

    queries = []
    if found:
        has_compound = bool(part_numbers and pattern_matches)
        if has_compound:
            # Primary term: first part number + first pattern match combined.
            compound = f"{part_numbers[0]} {pattern_matches[0]}"
            # Breadth terms: remaining pattern matches (not part numbers).
            breadth_terms = [t for t in pattern_matches[1:]]
            compound_quota = max(1, round(max_queries * _COMPOUND_QUOTA_FRAC))
            breadth_quota = max_queries - compound_quota

            # Build compound queries
            si = 0
            for _ in range(compound_quota):
                if si >= len(suffix_indices):
                    break
                suffix = ALL_HINT_SUFFIXES[suffix_indices[si]]
                queries.append(f"{compound} {suffix}")
                si += 1

            # Build breadth fallback queries (pattern-match only)
            if breadth_terms:
                for bt in breadth_terms:
                    if si >= len(suffix_indices) or len(queries) >= max_queries:
                        break
                    suffix = ALL_HINT_SUFFIXES[suffix_indices[si]]
                    queries.append(f"{bt} {suffix}")
                    si += 1
        else:
            # Original behavior: spread suffixes across all terms equally.
            total_terms = len(found)
            quotas = []
            for ti in range(total_terms):
                base = max_queries // total_terms
                extra = 1 if ti < (max_queries - base * total_terms) else 0
                quotas.append(base + extra)
            term_counts = [0] * total_terms
            si = 0
            while len(queries) < max_queries:
                added = False
                for ti in range(total_terms):
                    if term_counts[ti] < quotas[ti] and si < len(suffix_indices):
                        term = found[ti]
                        suffix = ALL_HINT_SUFFIXES[suffix_indices[si]]
                        queries.append(f"{term} {suffix}")
                        si += 1
                        term_counts[ti] += 1
                        added = True
                        if len(queries) >= max_queries:
                            break
                if not added:
                    break

    if not queries:
        queries = [prompt[:80]]

    return queries


# --------------------------------------------------------------------------
# Step 2 & 3: search + scrape
# --------------------------------------------------------------------------

class FirecrawlResearcher:
    def __init__(self, api_key: Optional[str] = None):
        api_key = api_key or os.environ.get("FIRECRAWL_API_KEY")
        if not api_key:
            raise ValueError(
                "No Firecrawl API key found. Set FIRECRAWL_API_KEY env var "
                "or pass api_key= explicitly."
            )
        self.client = Firecrawl(api_key=api_key)

    # -- search ------------------------------------------------------------
    def search_sources(self, query: str, limit: int = 5) -> List[Dict]:
        """Run a Firecrawl web search and return raw result dicts."""
        try:
            result = self.client.search(query, limit=limit)
        except Exception as e:
            return [{"url": None, "title": None, "error": str(e)}]

        # firecrawl-py v2 returns an object with `.web` (list) in most
        # versions; fall back to treating result as a plain list/dict.
        items = getattr(result, "web", None)
        if items is None:
            items = result if isinstance(result, list) else result.get("web", [])

        out = []
        for item in items[:limit]:
            url = getattr(item, "url", None) or item.get("url")
            title = getattr(item, "title", None) or item.get("title", "")
            out.append({"url": url, "title": title})
        return out

    # -- scrape --------------------------------------------------------------
    def scrape_source(self, url: str, query: str = "", prompt: str = "",
                      context_terms: Optional[List[str]] = None) -> Source:
        """Scrape a single URL for markdown + html, then pull out images."""
        host = urlparse(url).hostname or ""

        # DigiKey: JS-rendered parametric table — try action-enabled
        # approaches first, then fall back through progressively simpler
        # strategies (API → WaitAction → only_main_content=False → JS → basic).
        if "digikey" in host:
            doc = self._digikey_scrape(url, query=query, context_terms=context_terms)
            if doc is None:
                return Source(url=url, query=query,
                              error="All DigiKey scrape attempts failed")
        else:
            # --- Standard scrape strategy ---
            actions = None
            only_main = True
            exclude = None

            # Stack Exchange domains: disable main-content-only to keep vote
            # elements in the HTML, and exclude chrome (topbar, sidebars, etc.)
            if re.search(r'\.(stackexchange|stackoverflow|superuser|serverfault|askubuntu)\.', host):
                only_main = False
                exclude = [
                    ".s-topbar",
                    ".left-sidebar",
                    ".js-announcement-banner",
                    "nav",
                    "footer",
                    "header",
                ]

            # Per-domain / generic expand actions (click collapsed content)
            actions = _build_expand_actions(host)

            # --- Try scrape with retry on Fire Engine errors ---
            try:
                doc = self.client.scrape(
                    url,
                    formats=["markdown", "html", "links"],
                    only_main_content=only_main,
                    exclude_tags=exclude,
                    actions=actions,
                )
            except Exception as e:
                err_str = str(e)
                if actions:
                    try:
                        doc = self.client.scrape(
                            url,
                            formats=["markdown", "html", "links"],
                            only_main_content=only_main,
                            exclude_tags=exclude,
                            actions=None,
                        )
                    except Exception as e2:
                        return Source(url=url, query=query, error=str(e2))
                else:
                    return Source(url=url, query=query, error=err_str)

        markdown = getattr(doc, "markdown", None) or (
            doc.get("markdown", "") if isinstance(doc, dict) else ""
        )
        html = getattr(doc, "html", None) or (
            doc.get("html", "") if isinstance(doc, dict) else ""
        )

        # --- Anti-bot / Cloudflare challenge detection ---
        # These pages return a JS-challenge interstitial instead of real content.
        # Detect early so they surface as explicit failures, not empty successes.
        _challenge_title_re = re.compile(
            r"just a moment|attention required|cloudflare|verify you are human",
            re.IGNORECASE,
        )
        _challenge_body_re = re.compile(
            r"enable javascript and cookies to continue|cf-chl|checking your browser"
            r"|ray id|challenge-platform|turnstile|captcha|verify.*human",
            re.IGNORECASE,
        )
        _meta_title = ""
        meta_tmp = getattr(doc, "metadata", None) or (
            doc.get("metadata", {}) if isinstance(doc, dict) else {}
        )
        if meta_tmp:
            _meta_title = getattr(meta_tmp, "title", None) or (
                meta_tmp.get("title", "") if isinstance(meta_tmp, dict) else ""
            )
        _check_text = f"{_meta_title} {markdown[:500]}"
        if _challenge_title_re.search(_meta_title) or _challenge_body_re.search(_check_text):
            return Source(url=url, query=query,
                          error="Blocked by anti-bot challenge (Cloudflare/similar)")

        # Parse Stack Exchange vote counts from raw HTML before markdown
        # conversion loses the DOM structure.  Only for SE-family domains.
        votes_map = None
        if re.search(r'\.(stackexchange|stackoverflow|superuser|serverfault|askubuntu)\.', host):
            votes_map = _parse_se_votes(html or "")

        # Unwrap clickable images: [![alt](img)](link) -> ![alt](img).  Many
        # forum softwares wrap embedded images in a link (e.g. to a "click to
        # enlarge" or registration page).  For our inline research report the
        # link is noise, so we strip it unconditionally.
        markdown = re.sub(r'\[(!\[[^\]]*\]\([^)]+\))\]\([^)]+\)', r'\1', markdown)

        # Post-process: strip XenForo "Click to expand..." boilerplate.
        # The content inside bbCodeBlock-expandContent divs is already fully
        # present in the raw HTML -- the label is just UI chrome that pollutes
        # the markdown output.  Scoped to AAC because the string is unlikely
        # in natural prose, but theoretically possible.
        if "allaboutcircuits.com" in host:
            markdown = re.sub(
                r'^[>\s]*Click to expand\.\.\.\s*$',
                '', markdown, flags=re.MULTILINE,
            )
            markdown = re.sub(
                r'^[>\s]*<circuit diagram>\s*$',
                '', markdown, flags=re.MULTILINE,
            )

        meta = getattr(doc, "metadata", None) or (
            doc.get("metadata", {}) if isinstance(doc, dict) else {}
        )
        title = ""
        if meta:
            title = getattr(meta, "title", None) or (
                meta.get("title", "") if isinstance(meta, dict) else ""
            )

        # Interleaved pass: text and images are extracted TOGETHER, in the
        # order they actually appear on the page, so an image never loses
        # the paragraph that gives it context.
        content, images = extract_interleaved_content(
            markdown or "", prompt, max_chars=12000,
            context_terms=context_terms,
            votes_map=votes_map,
        )

        # Strip Firecrawl's <Base64-Image-Removed> placeholders — these are
        # XenForo emoticons whose actual base64 data was stripped; they produce
        # broken ![alt](<Base64-Image-Removed>) refs that Streamlit can't render.
        content = re.sub(
            r'!\[[^\]]*\]\(<Base64-Image-Removed>\)', '', content,
        )

        # Repair LaTeX math that Firecrawl mangled during PDF scraping
        # (double-escaped backslashes, escaped underscores inside $...$ blocks).
        content = _fix_latex_math(content)

        # Safety net: some pages embed images (lazy-loaded, CSS background,
        # <picture> tags) that Firecrawl's markdown conversion drops even
        # though they're present in the raw HTML. Sweep the HTML too and
        # append anything relevant that isn't already captured above, so a
        # real schematic never gets silently lost just because it didn't
        # survive the markdown conversion. Unlike the interleaved pass,
        # this sweep has no surrounding paragraph to judge context from --
        # so it requires an actual positive keyword signal (score > 0)
        # rather than just "not obviously junk", to avoid pulling in
        # unrelated page furniture that never had a chance to be filtered
        # by context in the first place.
        already_have = {_normalize_image_url(img.url) for img in images}
        html_images = [
            img for img in extract_images(html or "", "", context_terms=context_terms,
                                          base_url=url)
            if _normalize_image_url(img.url) not in already_have and img.relevance_score > 0
        ]
        if html_images:
            extra_block = "\n\n**📎 Additional images found on this page:**\n\n" + "\n\n".join(
                f"![{img.alt or 'image'}]({img.url})" for img in html_images[:15]
            )
            content = (content + extra_block) if content else extra_block.strip()
            images = images + html_images[:15]

        # -- PDF figure extraction ------------------------------------------------
        # Firecrawl extracts text from PDFs but drops all embedded images.
        # We fetch the raw PDF and run PyMuPDF ourselves to recover figures.
        pdf_dir = CACHE_DIR / "pdf_figures"
        if _HAS_PDF and url.lower().endswith(".pdf"):
            try:
                resp = requests.get(url, timeout=30)
                if resp.status_code == 200 and len(resp.content) > 1000:
                    pdf_figures = extract_pdf_figures(resp.content)
                    saved = save_figures(pdf_figures, str(pdf_dir))
                    already_have = {_normalize_image_url(img.url) for img in images}

                    # Score each figure using caption + nearby body text for
                    # richer relevance signals than the bare caption alone.
                    scored_pdf = []
                    for entry in saved:
                        local_path = entry["filepath"]
                        if local_path in already_have:
                            continue
                        score_text = f"{entry['caption']} {entry.get('context_text', '')}"
                        score = _score_image("", score_text, context_terms)
                        if score > 0:
                            scored_pdf.append((entry, score))

                    # Try to splice each figure inline at its caption position
                    # in content, so extract_circuit_entries() picks up real
                    # neighboring paragraphs as context.  Fall back to a
                    # trailing block for figures whose caption doesn't appear.
                    inline_count = 0
                    trailing: List[str] = []
                    for entry, score in scored_pdf:
                        caption = entry["caption"]
                        local_path = entry["filepath"]
                        img_md = f"\n\n![{caption}]({local_path})\n\n"

                        # Try to find "Figure N" substring in content
                        fig_match = re.search(
                            r"fig(?:ure)?\.?\s*\d+", caption, re.IGNORECASE
                        )
                        inserted = False
                        if fig_match and content:
                            search = fig_match.group(0)
                            # Find the line containing this figure reference
                            for line_match in re.finditer(
                                r"(?m)^.*" + re.escape(search) + r".*$", content
                            ):
                                insert_pos = line_match.end()
                                content = (
                                    content[:insert_pos] + img_md + content[insert_pos:]
                                )
                                inserted = True
                                inline_count += 1
                                break

                        if not inserted:
                            trailing.append(f"![{caption}]({local_path})")

                        images.append(
                            ScrapedImage(url=local_path, alt=caption, relevance_score=score)
                        )

                    if trailing:
                        extra = "\n\n**📎 Figures extracted from PDF:**\n\n" + "\n\n".join(trailing)
                        content = (content + extra) if content else extra.strip()

                    if scored_pdf:
                        print(
                            f"PDF figures for {url}: {inline_count} inline, "
                            f"{len(trailing)} trailing",
                            file=sys.stderr,
                        )

                    # -- PDF table extraction via PyMuPDF --
                    # Firecrawl flattens tables into run-on paragraphs; use
                    # PyMuPDF's find_tables() to recover structured tables.
                    try:
                        pdf_tables = extract_pdf_tables(resp.content)
                        table_inline = 0
                        table_trailing: List[str] = []
                        for t in pdf_tables:
                            md_table = t["markdown"]
                            header = t["header"]
                            # Try to find a section heading that matches the
                            # table's first header cell or a nearby keyword.
                            search_term = header[0] if header else ""
                            inserted = False
                            if search_term and content:
                                for hm in re.finditer(
                                    r"(?m)^#+\s*.*" + re.escape(search_term) + r".*$",
                                    content, re.IGNORECASE,
                                ):
                                    insert_pos = hm.end()
                                    content = (
                                        content[:insert_pos]
                                        + "\n\n" + md_table + "\n\n"
                                        + content[insert_pos:]
                                    )
                                    inserted = True
                                    table_inline += 1
                                    break
                            if not inserted:
                                table_trailing.append(md_table)

                        if table_trailing:
                            tbl_extra = (
                                "\n\n**📊 Extracted tables:**\n\n"
                                + "\n\n".join(table_trailing)
                            )
                            content = (content + tbl_extra) if content else tbl_extra.strip()

                        if pdf_tables:
                            print(
                                f"PDF tables for {url}: {table_inline} inline, "
                                f"{len(table_trailing)} trailing",
                                file=sys.stderr,
                            )
                    except Exception as te:
                        print(f"PDF table extraction failed for {url}: {te}", file=sys.stderr)
            except Exception as e:
                print(f"PDF figure extraction failed for {url}: {e}", file=sys.stderr)

        return Source(
            url=url,
            title=title or url,
            query=query,
            markdown=content,
            images=images,
        )

    def _digikey_scrape(self, url: str, query: str = "",
                        context_terms: Optional[List[str]] = None):
        """Fallback chain for DigiKey pages (API → scrape approaches)."""

        # e) Product Information API — fastest and most reliable when the
        #    client credentials are configured and the API is subscribed.
        #    Must run before any scrape attempt; exits early on success.
        dk_id = os.environ.get("DIGIKEY_CLIENT_ID", "")
        dk_secret = os.environ.get("DIGIKEY_CLIENT_SECRET", "")
        if dk_id and dk_secret:
            try:
                pn = _digikey_part_from_url(url) or _digikey_part_from_query(
                    query, context_terms
                )
                if pn:
                    from digikey_api import DigiKeyClient, DigiKeyNotFoundError, \
                        DigiKeyAuthError, make_markdown
                    # Try production first, then sandbox
                    for base in ("api.digikey.com", "sandbox-api.digikey.com"):
                        client = DigiKeyClient(
                            client_id=dk_id, client_secret=dk_secret,
                            base_url=base,
                        )
                        try:
                            details = client.get_product_details(pn)
                            markdown = make_markdown(details)
                            # Also include a JSON metadata block for the
                            # structured data export
                            meta = {"title": f"{pn} — DigiKey Product Details"}
                            print(f"DigiKey: API served data for {pn} "
                                  f"via {base}")
                            return {
                                "markdown": markdown,
                                "html": "",
                                "images": [],
                                "metadata": meta,
                            }
                        except DigiKeyAuthError as e:
                            print(f"DigiKey: API auth failed on {base} "
                                  f"— {e}", file=sys.stderr)
                            continue
                        except DigiKeyNotFoundError:
                            return Source(
                                url=url, query=query,
                                error=f"Part '{pn}' not found in DigiKey catalogue"
                            )
            except Exception as e:
                print(f"DigiKey: API approach failed — {e}",
                      file=sys.stderr)

        # a) WaitAction: wait for product table rows to appear
        if WaitAction is not None:
            try:
                return self.client.scrape(
                    url, formats=["markdown", "html", "links"],
                    only_main_content=False,
                    actions=[WaitAction(
                        selector="[data-testid='BPN-Product-Table'] tr"
                    )],
                )
            except Exception as e:
                print(f"DigiKey: WaitAction(selector=) failed — {e}",
                      file=sys.stderr)

        # b) only_main_content=False — captures skeleton HTML at least
        try:
            return self.client.scrape(
                url, formats=["markdown", "html", "links"],
                only_main_content=False,
            )
        except Exception as e:
            print(f"DigiKey: only_main_content=False failed — {e}",
                  file=sys.stderr)

        # c) ExecuteJavascriptAction: poll for table data to load
        if ExecuteJavascriptAction is not None:
            try:
                return self.client.scrape(
                    url, formats=["markdown", "html", "links"],
                    only_main_content=False,
                    actions=[ExecuteJavascriptAction(script="""
                        new Promise(r => {
                            let i = setInterval(() => {
                                let rows = document.querySelectorAll(
                                    '[data-testid="BPN-Product-Table"] tr, '
                                    '[data-testid="BPN-Product-Table"] td'
                                );
                                if (rows.length > 0) { clearInterval(i); r(); }
                            }, 200);
                            setTimeout(r, 15000);
                        });
                    """)],
                )
            except Exception as e:
                print(f"DigiKey: ExecuteJavascriptAction failed — {e}",
                      file=sys.stderr)

        # d) Final fallback: basic scrape with only_main_content=True
        try:
            print("DigiKey: all action/JS approaches failed, "
                  "falling back to only_main_content=True",
                  file=sys.stderr)
            return self.client.scrape(
                url, formats=["markdown", "html", "links"],
                only_main_content=True,
            )
        except Exception:
            return None

    # -- full pipeline -------------------------------------------------------
    def run_pipeline(
        self,
        prompt: str,
        max_queries: int = 6,
        results_per_query: int = 4,
        max_sources_to_scrape: int = 12,
        progress_cb=None,
        grok_api_key: Optional[str] = None,
    ) -> ResearchCorpus:
        """
        End-to-end: prompt -> queries -> search -> scrape -> corpus.
        progress_cb(stage: str, current: int, total: int) is called for
        UI progress bars (e.g. from Streamlit).
        """
        queries = generate_search_queries(prompt, max_queries=max_queries)
        context_terms = _extract_context_terms(prompt)

        candidates: List[Dict] = []
        search_errors: List[str] = []
        for i, q in enumerate(queries):
            if progress_cb:
                progress_cb("searching", i + 1, len(queries))
            results = self.search_sources(q, limit=results_per_query)
            for r in results:
                if r.get("url"):
                    r["query"] = q
                    candidates.append(r)
                elif r.get("error"):
                    search_errors.append(f"Search '{q}': {r['error']}")

        # Hard-exclude e-commerce / shopping domains before ranking.
        before = len(candidates)
        candidates = [
            c for c in candidates
            if not any(
                ed in urlparse(c.get("url", "")).netloc.replace("www.", "")
                for ed in EXCLUDED_DOMAINS
            )
        ]
        if before - len(candidates) > 0:
            print(f"  [filter] excluded {before - len(candidates)} e-commerce URLs")

        candidates = _dedupe_and_rank(
            candidates,
            part_numbers=[t for t in context_terms if any(c.isdigit() for c in t)],
        )[:max_sources_to_scrape]

        sources: List[Source] = []
        for i, c in enumerate(candidates):
            if progress_cb:
                progress_cb("scraping", i + 1, len(candidates))
            src = self.scrape_source(c["url"], query=c.get("query", ""), prompt=prompt,
                                     context_terms=context_terms)
            if not src.title:
                src.title = c.get("title", src.url)
            sources.append(src)

        corpus = ResearchCorpus(prompt=prompt, queries=queries, sources=sources,
                               search_errors=search_errors)

        if grok_api_key:
            if progress_cb:
                progress_cb("summarizing", 1, 1)
            summarizer = GrokSummarizer(api_key=grok_api_key)
            result = summarizer.summarize_corpus(corpus)
            if result:
                corpus.summary = result.get("text")
                corpus.summary_error = result.get("error")

        _save_cache(corpus)
        return corpus


class GrokSummarizer:
    """
    Optional AI Summary pass using xAI's Grok API (OpenAI-compatible
    /chat/completions endpoint). This never blocks or breaks the rest of
    the pipeline -- if there's no key, or the call fails, run_pipeline()
    just leaves corpus.summary as None and everything else still works.
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("GROK_API_KEY")

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def summarize_corpus(self, corpus: "ResearchCorpus", max_sources: int = 8) -> Optional[Dict]:
        if not self.enabled:
            return None
        ok_sources = [s for s in corpus.sources if not s.error][:max_sources]
        if not ok_sources:
            return None

        digest = "\n\n".join(
            f"### {s.title} ({s.url})\n{s.markdown[:2500]}" for s in ok_sources
        )
        system_prompt = (
            "You are an electronics design research assistant. Given scraped "
            "application notes, datasheets, and reference designs relevant to "
            "a PCB/circuit design prompt, produce a concise, technically "
            "specific digest an EE could act on. Respond ONLY in this exact "
            "markdown structure, no preamble, no extra commentary:\n\n"
            "## 🤖 AI Summary\n<3-5 sentence synthesis across all sources>\n\n"
            "## 🎯 Key Design Considerations\n<4-6 bullet points, specific "
            "and technical -- topologies, component choices, tradeoffs>\n\n"
            "## 📐 Recommended Approach\n<2-4 bullet points>\n\n"
            "## 💡 Key Takeaways\n<3-5 bullet points>"
        )
        user_prompt = (
            f"Design prompt:\n{corpus.prompt}\n\n"
            f"Scraped source material:\n{digest[:18000]}"
        )
        try:
            resp = requests.post(
                f"{GROK_API_BASE}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": GROK_MODEL,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 1200,
                },
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()
            text = data["choices"][0]["message"]["content"]
            return {"text": text}
        except Exception as e:
            return {"error": f"Grok summary failed: {e}"}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

IMG_TAG_RE = re.compile(r'<img[^>]+src=["\']([^"\']+)["\'][^>]*>', re.IGNORECASE)
ALT_RE = re.compile(r'alt=["\']([^"\']*)["\']', re.IGNORECASE)
MD_IMG_RE = re.compile(r'!\[([^\]]*)\]\(([^)\s]+)')
IMG_DIM_RE = re.compile(r'(?:width|height)=["\']?(\d+)["\']?', re.IGNORECASE)

RELEVANCE_KEYWORDS = [
    "schematic", "circuit", "diagram", "block diagram", "pinout",
    "waveform", "topology", "layout", "pcb", "wiring",
    "graph", "chart", "plot", "curve", "figure",
]

# Common filler words that happen to be 4+ letters (the threshold used to
# pull "significant" words out of the prompt) but carry no real technical
# signal -- without excluding these, a totally unrelated sentence that
# merely contains "with" or "from" can score as if it matched the prompt.
STOPWORDS = {
    "with", "from", "that", "this", "these", "those", "when", "where",
    "have", "will", "your", "into", "only", "also", "such", "than",
    "then", "them", "they", "were", "been", "being", "each", "some",
    "more", "most", "other", "which", "while", "about", "after",
    "before", "over", "under", "between", "using", "used", "used.",
    "here", "there", "what", "very", "just", "like", "make", "made",
}

# Filenames/alt-text containing these almost never point to an actual
# schematic/circuit image, even when they carry no positive keyword either
# -- product marketing renders, conference/booth photos, author headshots,
# "related articles" carousel thumbnails, ads, and social-share cards.
JUNK_IMAGE_KEYWORDS = [
    "logo", "icon", "avatar", "sprite", "banner", "hero", "promo",
    "campaign", "press-release", "press_release", "booth", "conference",
    "event", "team-photo", "team_photo", "headshot", "portrait", "author",
    "staff", "thumbnail", "thumb", "og-image", "og_image", "social-share",
    "social_share", "card-image", "card_image", "related-article",
    "related_article", "recommend", "sponsor", "advert", "stock-photo",
    "stockphoto",
    # Social login / share button icons (XenForo, etc.)
    "facebook", "google", "github", "linkedin", "twitter", "youtube",
    "instagram", "wechat", "weibo", "reddit",
    # Lazy-loading placeholder images
    "loading",
]

_MD_LINK_ONLY_RE = re.compile(r"^\s*[-*]?\s*\[([^\]]+)\]\(([^)]+)\)\s*$")
_MD_LINK_RE = re.compile(r"\[[^\]]+\]\([^)]+\)")


def _normalize_image_url(url: str) -> str:
    """Normalize an image URL for deduplication: strip tracking query params,
    normalize http/https, remove trailing slashes."""
    from urllib.parse import urlparse, urlunparse, parse_qs, urlencode
    parsed = urlparse(url)
    # Normalize scheme and host — always prefer https
    scheme = "https"
    host = parsed.hostname.lower() if parsed.hostname else ""
    # Strip common tracking params
    tracking_params = {"v", "version", "t", "ts", "timestamp", "cache",
                       "imageView2", "x-oss-process", "w", "h", "quality"}
    qs = parse_qs(parsed.query, keep_blank_values=False)
    clean_qs = {k: v for k, v in qs.items() if k.lower() not in tracking_params}
    clean_query = urlencode(clean_qs, doseq=True) if clean_qs else ""
    # Rebuild with normalized path (strip trailing slash for consistency)
    path = parsed.path.rstrip("/")
    return urlunparse((scheme, host, path, parsed.params, clean_query, ""))


def _is_nav_row(stripped_line: str) -> bool:
    """A line that's nothing but markdown links + separators (|, ,, ·) is a
    nav/breadcrumb row, e.g. '[Home](/) | [About](/about) | [Contact](/c)'."""
    if not _MD_LINK_RE.search(stripped_line):
        return False
    remainder = _MD_LINK_RE.sub("", stripped_line)
    return not re.sub(r"[|,·\-\s]", "", remainder)

# A number followed by an EE-relevant unit is a strong "this paragraph has
# real technical content" signal (e.g. "100 nV", "2 mA", "10 kOhm").
_UNIT_NUMBER_RE = re.compile(
    r"\d+(\.\d+)?\s*(m?v|m?a|u?a|n?a|p?a|f?a|ohm|hz|khz|mhz|ghz|db|nf|pf|uf|"
    r"bit|sps|ppm|ppb|degc|°c)\b",
    re.IGNORECASE,
)


def _split_blocks(markdown: str) -> List[str]:
    """Split markdown into blocks on blank lines, preserving original order.
    A 'block' is a paragraph, heading, table row-group, or inline image —
    whatever markdown naturally groups between blank lines."""
    raw_blocks = re.split(r"\n\s*\n", markdown or "")
    return [b.strip() for b in raw_blocks if b.strip()]


def _block_images(block: str, context_terms: Optional[List[str]] = None) -> List["ScrapedImage"]:
    imgs = []
    for m in MD_IMG_RE.finditer(block):
        alt, url = m.group(1), m.group(2)
        if '<Base64-Image-Removed>' in url:
            continue
        imgs.append(ScrapedImage(url=url, alt=alt, relevance_score=_score_image(url, alt, context_terms)))
    return imgs


def _block_text_score(block: str, prompt_terms, prompt_words, context_terms=None,
                      vote_count: int = 0) -> int:
    lower = block.lower()
    score = 0
    for term in prompt_terms:
        if term in lower:
            score += 3
    # Prompt-specific context terms (part numbers, matched circuit types) get
    # a much higher boost — they're the exact thing being designed around.
    if context_terms:
        for term in context_terms:
            if term.lower() in lower:
                score += 8
    score += sum(1 for w in prompt_words if w in lower)
    score += sum(1 for kw in RELEVANCE_KEYWORDS if kw in lower)
    if _UNIT_NUMBER_RE.search(lower):
        score += 2
    if block.startswith("#") or "|" in block:
        score += 1
    # Stack Exchange / forum upvote bonus: +1 per 5 upvotes, capped at +15
    if vote_count > 0:
        score += min(vote_count // 5, 15)
    return score


def extract_interleaved_content(
    markdown: str, prompt: str, max_chars: int = 9000,
    context_terms: Optional[List[str]] = None,
    votes_map: Optional[Dict[str, Dict]] = None,
):
    """
    Walk the scraped markdown top-to-bottom and keep the blocks (paragraphs,
    headings, tables, AND inline images) relevant to the design prompt --
    in their ORIGINAL reading order.

    This is the key difference from scraping text and images separately:
    an image stays attached to whatever paragraph was actually talking
    about it, instead of getting dumped into a disconnected gallery. It's
    also why a relevant image whose *own* alt text is weak (e.g. a
    schematic with alt="Figure 3") still gets kept -- it inherits
    relevance from the surrounding paragraph via the neighbor-context step
    below, instead of being scored purely in isolation.

    Returns (interleaved_markdown, images_kept) so callers get one ready
    to render/save string plus a flat image list for stats/galleries.
    """
    cleaned = _clean_boilerplate(markdown)
    blocks = _split_blocks(cleaned)
    blocks = _dedupe_blocks(blocks)
    if not blocks:
        return "", []

    prompt_lower = (prompt or "").lower()
    prompt_terms = set()
    for pattern in EE_KEYWORD_PATTERNS:
        m = re.search(pattern, prompt_lower, flags=re.IGNORECASE)
        if m:
            prompt_terms.add(m.group(0).lower())
    prompt_words = {w.lower() for w in re.findall(r"[A-Za-z]{4,}", prompt or "")} - STOPWORDS

    # classify + score every block, keeping original index for reordering
    scored = []  # (idx, kind, score, block, images_in_block)
    for idx, block in enumerate(blocks):
        imgs = _block_images(block, context_terms)
        text_without_imgs = MD_IMG_RE.sub("", block).strip()
        is_image_block = bool(imgs) and len(text_without_imgs) < 20
        if is_image_block:
            score = max((i.relevance_score for i in imgs), default=0)
            scored.append((idx, "image", score, block, imgs))
        else:
            if len(block) < 40:
                continue  # stray menu items / bare links, not real content
            vote_info = _match_block_to_vote(block, votes_map or {})
            vote_count = vote_info.get("votes", 0)
            score = _block_text_score(block, prompt_terms, prompt_words,
                                      context_terms, vote_count=vote_count)
            scored.append((idx, "text", score, block, imgs))

    if not scored:
        return cleaned[:max_chars], []

    # Text blocks: keep if they scored positively against the prompt.
    #
    # Image blocks are trickier: an image's own alt text is often blank or
    # generic ("Figure 3", "img_042.png") even when it's a real schematic,
    # so requiring a positive keyword match on the image alone drops real
    # circuit diagrams. But being too permissive the other way (keep any
    # image that isn't *explicitly* junk, anywhere on the page) lets
    # unrelated stuff through too -- product marketing renders, "related
    # articles" carousel thumbnails, event photos -- none of which happen
    # to contain a junk keyword either.
    #
    # The fix is context: a neutral-scoring image is only kept if it's
    # actually sitting near text that's genuinely relevant to the prompt.
    # An image with its OWN positive signal (alt/filename says
    # "schematic") is always kept regardless of neighbors. An image that's
    # explicitly junk (logo, banner, headshot, ...) is never kept.
    def _nearby_text_score(pos: int, window: int = 2) -> int:
        best = 0
        for offset in range(1, window + 1):
            for neighbor_pos in (pos - offset, pos + offset):
                if 0 <= neighbor_pos < len(scored):
                    n_idx, n_kind, n_score, n_block, n_imgs = scored[neighbor_pos]
                    if n_kind == "text":
                        best = max(best, n_score)
        return best

    keep = set()
    for pos, (idx, kind, score, block, imgs) in enumerate(scored):
        if kind == "text":
            if score > 0:
                keep.add(idx)
        else:  # image
            if score > 0:
                keep.add(idx)  # own alt/filename gives a real signal
            elif score == 0 and _nearby_text_score(pos) > 0:
                keep.add(idx)  # neutral, but sits near relevant content
            # score < 0 (explicit junk) is never kept, regardless of context

    # nothing matched the prompt at all -- fall back to the longest blocks
    # rather than returning an empty source
    if not keep:
        longest = sorted(scored, key=lambda s: len(s[3]), reverse=True)[:10]
        keep = {s[0] for s in longest}

    # give every kept image one block of surrounding TEXT as context (its
    # caption or lead-in sentence) so it never shows up floating with no
    # explanation around it. Deliberately one-directional: a relevant
    # paragraph should NOT pull in a neighboring image just because it's
    # adjacent (that's how logos/icons next to real content used to sneak
    # in) -- only images pull in text, never the reverse.
    for pos, (idx, kind, score, block, imgs) in enumerate(scored):
        if kind == "image" and idx in keep:
            for neighbor_pos in (pos - 1, pos + 1):
                if 0 <= neighbor_pos < len(scored):
                    n_idx, n_kind, n_score, n_block, n_imgs = scored[neighbor_pos]
                    if n_kind == "text":
                        keep.add(n_idx)

    # Respect the character budget for TEXT only -- images are exempt from
    # the budget entirely (capped at a generous max_images instead) so a
    # long page never causes a real schematic to get trimmed out purely
    # because text elsewhere ate the budget first.
    by_score = sorted(scored, key=lambda s: s[2], reverse=True)
    budget_keep, total = set(), 0
    image_count = 0
    max_images = 20
    for idx, kind, score, block, imgs in by_score:
        if idx not in keep:
            continue
        if kind == "image":
            if image_count >= max_images:
                continue
            budget_keep.add(idx)
            image_count += 1
            continue
        if total + len(block) > max_chars and budget_keep:
            continue
        budget_keep.add(idx)
        total += len(block)

    final_indices = sorted(budget_keep)
    kept_blocks = [blocks[i] for i in final_indices]
    kept_images = [
        img
        for idx, kind, score, block, imgs in scored
        if idx in budget_keep and kind == "image"
        for img in imgs
    ]

    return "\n\n".join(kept_blocks), kept_images


# --------------------------------------------------------------------------
# Circuits & Schematics gallery -- one card per image, full context attached
# --------------------------------------------------------------------------

def extract_circuit_entries(source: "Source") -> List[Dict]:
    """
    Build a flat, per-image list of "circuit card" entries from an already
    -interleaved Source.markdown, for a dedicated schematics/circuits view.

    Unlike the flowing research report (which mixes text and images into
    one continuous read), this pulls each image out with BOTH of its
    immediate neighboring text blocks attached IN FULL -- nothing
    truncated, nothing summarized -- so a schematic is never shown without
    the paragraph(s) that were actually describing it on the source page.

    Returns a list of dicts, one per image, in original reading order:
      {
        "heading":         nearest preceding markdown heading in the
                            source (falls back to the source title),
        "image_url":       image URL, or a local file path for PDF-
                            extracted figures,
        "alt":              image alt text / caption,
        "relevance_score":  int, carried over from the scored ScrapedImage,
        "context_before":   full text block immediately before the image
                             (empty string if none),
        "context_after":    full text block immediately after the image
                             (empty string if none),
        "source_title":     source.title,
        "source_url":       source.url,
        "query":            source.query,
      }
    """
    blocks = _split_blocks(source.markdown)
    if not blocks:
        return []

    def _is_image_block(b: str) -> bool:
        imgs = _block_images(b)
        return bool(imgs) and len(MD_IMG_RE.sub("", b).strip()) < 20

    entries: List[Dict] = []
    current_heading = source.title

    for i, block in enumerate(blocks):
        if block.startswith("#"):
            current_heading = block.lstrip("#").strip() or current_heading
            continue

        if not _is_image_block(block):
            continue

        context_before = ""
        for j in range(i - 1, -1, -1):
            b = blocks[j]
            if b.startswith("#") or _is_image_block(b):
                continue
            context_before = b
            break

        context_after = ""
        for j in range(i + 1, len(blocks)):
            b = blocks[j]
            if b.startswith("#") or _is_image_block(b):
                continue
            context_after = b
            break

        # Don't let noise-line context (login widgets, nav chrome, etc.)
        # inflate the score of junk images.
        if context_before and _NOISE_LINE_RE.search(context_before):
            context_before = ""
        if context_after and _NOISE_LINE_RE.search(context_after):
            context_after = ""

        for img in _block_images(block):
            # Always recompute image score — cached scores may be stale.
            img_score = _score_image(img.url, img.alt, context_terms=None)
            # Combine image score with text relevance of surrounding context
            # so cards with relevant text aren't shown as score-0 junk.
            text_score = 0
            if context_before:
                text_score += _block_text_score(context_before, [], [],
                                                context_terms=None)
            if context_after:
                text_score += _block_text_score(context_after, [], [],
                                                context_terms=None)
            combined_score = img_score
            if img_score >= 0:
                # Only boost neutral/positive images with text context.
                # Clearly-junk images (social icons, loading spinners)
                # should not be rescued by surrounding text.
                combined_score = max(img_score, text_score)
            entries.append({
                "heading": current_heading,
                "image_url": img.url,
                "alt": img.alt or "",
                "relevance_score": combined_score,
                "context_before": context_before,
                "context_after": context_after,
                "source_title": source.title,
                "source_url": source.url,
                "query": source.query,
            })

    return entries


def build_circuit_gallery(corpus: "ResearchCorpus") -> List[Dict]:
    """Run extract_circuit_entries() across every successfully-scraped
    source in the corpus and return one flat, ordered list -- the full
    data set behind the 'Circuits & Schematics' tab."""
    gallery: List[Dict] = []
    seen: set = set()
    for s in corpus.sources:
        if s.error:
            continue
        for entry in extract_circuit_entries(s):
            # Safety-net dedup: drop exact (image_url, context_before,
            # context_after) duplicates regardless of upstream root cause.
            key = (entry["image_url"], entry["context_before"], entry["context_after"])
            if key in seen:
                continue
            seen.add(key)
            # Also dedup by normalized image URL alone — if the same image
            # appears with different tracking params or http vs https.
            norm_url = _normalize_image_url(entry["image_url"])
            url_key = ("__url__", norm_url)
            if url_key in seen:
                continue
            seen.add(url_key)
            # Drop clearly-junk entries (social icons, loading spinners,
            # unrelated teasers) before they reach the UI.
            if entry["relevance_score"] <= 0:
                continue
            gallery.append(entry)
    return gallery


def extract_images(html: str, markdown: str = "", context_terms: Optional[List[str]] = None,
                   base_url: str = "") -> List[ScrapedImage]:
    found: Dict[str, ScrapedImage] = {}

    for m in IMG_TAG_RE.finditer(html or ""):
        src = m.group(0)
        url = _html.unescape(m.group(1))
        # Resolve relative / protocol-relative URLs against the page
        if base_url and not url.startswith(("http://", "https://", "data:")):
            url = urljoin(base_url, url)
        # Skip data: URIs and Firecrawl's base64-stripped placeholders
        if url.startswith("data:") or '<Base64-Image-Removed>' in url:
            continue
        alt_match = ALT_RE.search(src)
        alt = _html.unescape(alt_match.group(1)) if alt_match else ""
        # Parse width/height from HTML attributes (cheap, no network call)
        dims = IMG_DIM_RE.findall(src)
        w = int(dims[0]) if len(dims) >= 1 and dims[0].isdigit() else None
        h = int(dims[1]) if len(dims) >= 2 and dims[1].isdigit() else None
        found[url] = ScrapedImage(url=url, alt=alt,
                                  relevance_score=_score_image(url, alt, context_terms, w, h))

    for m in MD_IMG_RE.finditer(markdown or ""):
        alt, url = _html.unescape(m.group(1)), _html.unescape(m.group(2))
        if base_url and not url.startswith(("http://", "https://", "data:")):
            url = urljoin(base_url, url)
        if url.startswith("data:") or '<Base64-Image-Removed>' in url:
            continue
        if url not in found:
            found[url] = ScrapedImage(url=url, alt=alt, relevance_score=_score_image(url, alt, context_terms))

    images = list(found.values())
    images.sort(key=lambda i: i.relevance_score, reverse=True)
    return images


def _score_image(url: str, alt: str, context_terms: Optional[List[str]] = None,
                 width: Optional[int] = None, height: Optional[int] = None) -> int:
    text = f"{url} {alt}".lower()
    score = 0
    for kw in RELEVANCE_KEYWORDS:
        if kw in text:
            score += 2
    # Boost for prompt-specific context terms (part numbers, circuit types)
    if context_terms:
        for term in context_terms:
            if term.lower() in text:
                score += 5
    # Penalize obvious non-content images
    if any(kw in text for kw in JUNK_IMAGE_KEYWORDS):
        score -= 3
    # Small tracking pixels / svg sprites are rarely useful
    if url.lower().endswith((".svg",)) and "schematic" not in text:
        score -= 1
    # Soft penalty for banner/logo-shaped images based on HTML dimensions.
    # Skip if dimensions unknown — some real schematics are wide.
    if width and height and width > 0 and height > 0:
        aspect = width / height
        # Very wide + short = horizontal banner (e.g. 728x90 leaderboard)
        if aspect > 4 and width > 400:
            score -= 2
        # Very tall + narrow = sidebar ad / mobile banner
        if aspect < 0.25 and height > 400:
            score -= 2
        # Large hero image with no meaningful alt text
        if width > 1200 and len(alt.strip()) < 5:
            score -= 3
        # Tiny icon / tracking pixel
        if width < 50 or height < 50:
            score -= 1
    return score


# -- LaTeX repair ---------------------------------------------------------
# Firecrawl's PDF-to-markdown conversion double-escapes backslashes inside
# LaTeX math blocks, turning  $\Omega$  into  $\\Omega$  which Streamlit's
# MathJax renders as literal "backslash Omega" text.  It also sometimes
# escapes underscores (\\_ instead of \_), breaking subscripts.
#
# This pass finds $...$ and $$...$$ blocks and un-escapes them so MathJax
# can render the math properly.

_LATEX_INLINE_RE  = re.compile(r'(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)')
_LATEX_DISPLAY_RE = re.compile(r'\$\$(.+?)\$\$', re.DOTALL)


def _fix_latex_math(markdown: str) -> str:
    """
    Repair LaTeX math fragments that Firecrawl mangled during PDF scraping.

    Fixes applied inside $...$ and $$...$$ blocks:
      - Double-escaped backslashes:  \\\\Omega -> \\Omega  -> \\Omega (rendered)
      - Escaped underscores:         \\_       -> \\_
      - Stray spaces around content: $ \\Omega $ -> $\\Omega$

    Also repairs bare (un-delimited) LaTeX fragments like '5Omega5Omega'
    by deduplicating and wrapping in $...$.
    """
    if not markdown:
        return markdown

    def _clean_math_block(m: re.Match) -> str:
        """Un-escape a single LaTeX math block."""
        inner = m.group(1)
        # One level of un-escaping: \\alpha -> \alpha, \\_ -> \_
        inner = inner.replace('\\\\', '\\')
        inner = inner.replace('\\_', '_')
        # Strip spurious spaces that Firecrawl inserts inside $ delimiters
        inner = inner.strip()
        # Re-wrap in the original delimiter style
        full = m.group(0)
        if full.startswith('$$'):
            return f'$${inner}$$'
        return f'${inner}$'

    # Fix display math first ($$...$$), then inline ($...$)
    result = _LATEX_DISPLAY_RE.sub(_clean_math_block, markdown)
    result = _LATEX_INLINE_RE.sub(_clean_math_block, result)

    return result


# -- Stack Exchange vote parsing -------------------------------------------

def _parse_se_votes(html: str) -> Dict[str, Dict]:
    """
    Parse vote counts and accepted-answer status from Stack Exchange HTML.

    Returns a dict mapping a text fingerprint (first ~120 chars of the
    answer/comment body, normalised) to {"votes": int, "accepted": bool}.

    Uses BeautifulSoup selectors when available, falls back to text-based
    regex extraction if BS4 isn't installed or selectors fail.
    """
    if not html:
        return {}

    votes_map: Dict[str, Dict] = {}

    if _HAS_BS4:
        try:
            soup = BeautifulSoup(html, "html.parser")
            # Answers: look for post-layout containers
            for ans in soup.select(".answer, .post-layout"):
                # Vote count — primary selector, fallback text regex
                vc_el = ans.select_one(".js-vote-count")
                if vc_el is None:
                    vc_el = ans.select_one(".vote-count-post")
                vote_text = vc_el.get_text(strip=True) if vc_el else "0"
                try:
                    vote_count = int(vote_text)
                except (ValueError, TypeError):
                    vote_count = 0

                is_accepted = bool(
                    ans.select_one(".accepted-answer")
                    or ans.select_one(".s-accepted-indicator")
                    or ans.select_one('[class*="accepted"]')
                )

                # Answer body text
                body = ans.select_one(".post-text, .answercell, .s-prose")
                if body is None:
                    continue
                body_text = body.get_text(" ", strip=True)
                if len(body_text) < 30:
                    continue
                fingerprint = body_text[:120].lower()
                votes_map[fingerprint] = {
                    "votes": vote_count,
                    "accepted": is_accepted,
                }

            # Comments — less critical but still useful signals
            for cm in soup.select(".comment"):
                score_el = cm.select_one(".vote-count-post, .comment-score")
                vote_text = score_el.get_text(strip=True) if score_el else "0"
                try:
                    vote_count = int(vote_text)
                except (ValueError, TypeError):
                    vote_count = 0
                body = cm.select_one(".comment-text, .comment-body")
                if body is None:
                    continue
                body_text = body.get_text(" ", strip=True)
                if len(body_text) < 20:
                    continue
                fingerprint = body_text[:120].lower()
                votes_map[fingerprint] = {
                    "votes": vote_count,
                    "accepted": False,
                }
            return votes_map
        except Exception:
            pass  # fall through to regex fallback

    # --- Text-based fallback: extract "vote-count" numbers + nearby text ---
    # SE renders vote counts as plain numbers near answer text; the regex
    # approach can't perfectly align them but gives a reasonable approximation.
    vote_blocks = re.split(
        r'(?=\b(?:accepted|answer|comment)\b)', html, flags=re.IGNORECASE
    )
    for block in vote_blocks:
        nums = re.findall(r'(?<!\d)(\d{1,6})(?!\d)', block[:300])
        text_match = re.search(r'<p[^>]*>(.*?)</p>', block, re.DOTALL)
        if not text_match:
            continue
        body_text = re.sub(r'<[^>]+>', '', text_match.group(1)).strip()
        if len(body_text) < 30:
            continue
        fingerprint = body_text[:120].lower()
        vote_count = int(nums[0]) if nums else 0
        is_accepted = "accepted" in block[:200].lower()
        votes_map[fingerprint] = {"votes": vote_count, "accepted": is_accepted}

    return votes_map


def _match_block_to_vote(block_text: str, votes_map: Dict[str, Dict]) -> Dict:
    """Find the best matching vote entry for a markdown text block."""
    if not votes_map:
        return {"votes": 0, "accepted": False}
    text_lower = block_text.lower()[:120]
    # Direct prefix match
    for fp, info in votes_map.items():
        if text_lower.startswith(fp[:60]) or fp.startswith(text_lower[:60]):
            return info
    # Overlapping token match (>50% overlap)
    block_tokens = set(text_lower.split())
    best_match, best_overlap = None, 0
    for fp, info in votes_map.items():
        fp_tokens = set(fp.split())
        if not fp_tokens:
            continue
        overlap = len(block_tokens & fp_tokens) / max(len(fp_tokens), 1)
        if overlap > best_overlap and overlap > 0.5:
            best_overlap = overlap
            best_match = info
    return best_match or {"votes": 0, "accepted": False}


# -- Per-domain expand actions for Firecrawl --------------------------------

# Each entry maps a domain substring to a list of (selector, wait_ms) pairs
# that should be clicked before scraping to reveal hidden/collapsed content.
# The pipeline chains ClickAction+WaitAction for each pair, capped at 5 total.
_EXPAND_SELECTORS = {
    "stackexchange": [
        ("a.js-show-link", 1500),
    ],
    "stackoverflow": [
        ("a.js-show-link", 1500),
    ],
    "allaboutcircuits.com": [
        (".bbCodeBlock-expandContent", 1000),
        ("[data-toggle='bbCodeBlock-expandContent']", 1000),
    ],
    "reddit.com": [
        ("[data-click-events='toggle_comment']", 1500),
        ("button[aria-label='more comments']", 1500),
    ],
}
# Generic fallback selectors to try on any domain (cheap no-ops if absent)
_GENERIC_EXPAND_SELECTORS = [
    ("[aria-expanded='false']", 1000),
    ("button:has-text('Show more')", 800),
    ("button:has-text('Read more')", 800),
]
_MAX_CLICK_ACTIONS = 5


def _build_expand_actions(host: str) -> Optional[list]:
    """Build a list of ClickAction+WaitAction pairs for the given host."""
    if ClickAction is None or WaitAction is None:
        return None
    actions = []
    # Domain-specific selectors first
    for domain_key, selectors in _EXPAND_SELECTORS.items():
        if domain_key in host:
            for sel, wait_ms in selectors:
                actions.append(ClickAction(selector=sel))
                actions.append(WaitAction(milliseconds=wait_ms))
            break
    # Add generic fallbacks (up to cap)
    for sel, wait_ms in _GENERIC_EXPAND_SELECTORS:
        if len(actions) >= _MAX_CLICK_ACTIONS * 2:
            break
        actions.append(ClickAction(selector=sel))
        actions.append(WaitAction(milliseconds=wait_ms))
    if not actions:
        return None
    return actions[:_MAX_CLICK_ACTIONS * 2]


# -- Data cleaning: extended noise patterns --------------------------------

NOISE_LINE_PATTERNS = [
    r"cookie", r"we use cookies", r"accept all cookies", r"privacy policy",
    r"subscribe to our newsletter", r"sign in", r"log in", r"create an account",
    r"advertisement", r"sponsored", r"related articles", r"you may also like",
    r"share this", r"follow us on", r"skip to (main )?content", r"back to top",
    r"all rights reserved", r"terms of (use|service)",
    # Manufacturer datasheet boilerplate (repeat on every page)
    r"submit (?:documentation|document)\s+feedback",
    r"copyright\s*©?\s*\d{4}.*?(?:incorporated|inc\.?|corp\.?|ltd\.?|technology|semiconductor|company)",
    r"product folder links?:?",
    # Forum-specific noise: signatures, voting UI, widget cruft
    r"(?:was this (?:answer|article) helpful|did you find this useful)",
    r"(?:upvote|downvote|flag this|edit|share|improve this answer)",
    r"(?:related questions|you might also like|browse other questions)",
    r"(?:see (?:also|more)|related topics|more from (?:this|stack))",
    r"(?:sign up|log in|register|join this community)",
    r"(?:ask your own question|post as a guest|draft saved|draft discarded)",
    r"(?:edited \d+ (?:mins?|hours?|days?) ago|answered \d+ (?:mins?|hours?|days?) ago)",
    r"(?:thanks for (?:contributing|answering)|hope this helps)",
    r"(?:edited by|answered by|community wiki)",
    # Forum metadata: "Posted on [August 01st 2023 | 7:51 am](url)" —
    # requires pipe + am/pm to avoid matching normal prose like "Posted on the forum".
    # Uses \A (not ^) because this list is joined with | into one regex.
    r"\Aposted\s+(?:on|by)\b.+\|\s*\d+[:::\d]*\s*(?:am|pm)\b",
    # Datasheet revision-date header stamp: "LM386 SNAS450 – MAY 2004 – REVISED AUGUST 2023"
    # Requires "REVISED <month> <year>" suffix specifically — won't match normal prose.
    r"\A.*\b(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\w*\s+\d{4}\s+[-–—]\s+REVISED\s+(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\w*\s+\d{4}\s*\Z",
    # Login / social-widget chrome (XenForo, EEWorld, etc.)
    r"join our engineering community",
    r"sign[\s\-]?in with:?",
    # Standalone social-platform name (complete line, not inside a sentence)
    r"\A\s*(?:facebook|google|github|linkedin|twitter)\s*\Z",
    # Standalone bare digit line (like/view/comment counter with no label)
    r"\A\s*\d{1,6}\s*\Z",
    # EEWorld / Chinese electronics site widget chrome
    r"follow\s+eeworld",
    r"(?:next|previous)\s+article[：:]",
    # EEWorld download icon + link lines (short lines that are just an icon
    # image followed by a download link, no real content)
    r"!\[[^\]]*\]\([^)]*xzzxicon\.png\)",
]
_NOISE_LINE_RE = re.compile("|".join(NOISE_LINE_PATTERNS), re.IGNORECASE)


def _clean_boilerplate(markdown: str) -> str:
    """
    Strip cookie notices, nav/share/subscribe cruft, and runs of bare
    menu-style links from scraped markdown. Cheap, deterministic pass that
    runs on top of Firecrawl's own only_main_content filtering.
    """
    lines = (markdown or "").splitlines()
    cleaned = []
    consecutive_link_lines = 0

    for line in lines:
        stripped = line.strip()
        if not stripped:
            cleaned.append(line)
            consecutive_link_lines = 0
            continue

        if _NOISE_LINE_RE.search(stripped):
            continue

        if _is_nav_row(stripped):
            continue

        if _MD_LINK_ONLY_RE.match(stripped):
            consecutive_link_lines += 1
            # 3+ consecutive bare-link lines is almost always a nav/menu block
            if consecutive_link_lines >= 3:
                continue
        else:
            consecutive_link_lines = 0

        cleaned.append(line)

    return "\n".join(cleaned)


def _dedupe_blocks(blocks: List[str]) -> List[str]:
    """
    Drop near-duplicate blocks from a list of markdown blocks.
    Normalises whitespace and case before comparing; keeps the first occurrence.
    """
    seen: List[str] = []
    out: List[str] = []
    for block in blocks:
        # Strip trailing punctuation and normalise whitespace for comparison
        normalised = re.sub(r"\s+", " ", block.lower().strip())
        norm_stripped = re.sub(r"[.,;:!?]+$", "", normalised)
        is_dup = False
        for prev, prev_stripped in seen:
            if normalised == prev:
                is_dup = True
                break
            # Check prefix overlap on stripped text (>60 chars shared = dup)
            min_len = min(len(norm_stripped), len(prev_stripped))
            if min_len > 40:
                compare_len = min(min_len, 80)
                if norm_stripped[:compare_len] == prev_stripped[:compare_len]:
                    is_dup = True
                    break
        if not is_dup:
            seen.append((normalised, norm_stripped))
            out.append(block)
    return out


def extract_relevant_text(markdown: str, prompt: str, max_chars: int = 6000) -> str:
    """
    Rank paragraphs of scraped markdown by relevance to the design prompt
    (same spirit as _score_image for images) instead of just truncating
    from the top of the page, which usually grabs boilerplate.
    """
    cleaned = _clean_boilerplate(markdown)
    paragraphs = [p for p in re.split(r"\n\s*\n", cleaned)]
    paragraphs = _dedupe_blocks(paragraphs)

    prompt_lower = (prompt or "").lower()
    prompt_terms = set()
    for pattern in EE_KEYWORD_PATTERNS:
        m = re.search(pattern, prompt_lower, flags=re.IGNORECASE)
        if m:
            prompt_terms.add(m.group(0).lower())
    prompt_words = {w.lower() for w in re.findall(r"[A-Za-z]{4,}", prompt or "")} - STOPWORDS

    scored = []
    for idx, para in enumerate(paragraphs):
        text = para.strip()
        if len(text) < 40:
            # too short to be real content (stray menu items, single links)
            continue
        lower = text.lower()
        score = 0
        for term in prompt_terms:
            if term in lower:
                score += 3
        score += sum(1 for w in prompt_words if w in lower)
        score += sum(1 for kw in RELEVANCE_KEYWORDS if kw in lower)
        if _UNIT_NUMBER_RE.search(lower):
            score += 2
        # headings and tables often carry key specs even if short on keywords
        if text.startswith("#") or "|" in text:
            score += 1
        scored.append((idx, score, text))

    if not scored:
        return cleaned[:max_chars]

    kept = [s for s in scored if s[1] > 0]
    if not kept:
        # nothing matched keywords -- fall back to the longest paragraphs
        # rather than dropping the source's text entirely
        kept = sorted(scored, key=lambda s: len(s[2]), reverse=True)[:10]

    kept_by_score = sorted(kept, key=lambda s: s[1], reverse=True)
    selected, total = [], 0
    for idx, score, text in kept_by_score:
        if total >= max_chars:
            break
        selected.append((idx, text))
        total += len(text)

    # restore original reading order for coherence
    selected.sort(key=lambda s: s[0])
    return "\n\n".join(t for _, t in selected)


def _dedupe_and_rank(candidates: List[Dict], part_numbers: Optional[List[str]] = None) -> List[Dict]:
    seen = set()
    deduped = []
    for c in candidates:
        url = c.get("url")
        if not url or url in seen:
            continue
        seen.add(url)
        deduped.append(c)

    def rank(c):
        domain = urlparse(c["url"]).netloc.replace("www.", "")
        preferred = any(pd in domain for pd in PREFERRED_DOMAINS)
        deprioritized = any(dd in domain for dd in DEPRIORITIZED_DOMAINS)
        # PDFs and datasheet paths are the most information-dense sources
        is_pdf = c["url"].lower().endswith(".pdf") or "/datasheet" in c["url"].lower()
        # Check for exact part-number match in URL, title, or matched query
        url_title_query = f"{c.get('url', '')} {c.get('title', '')} {c.get('query', '')}"
        has_pn = False
        if part_numbers:
            lower_utq = url_title_query.lower()
            for pn in part_numbers:
                if pn.lower() in lower_utq:
                    has_pn = True
                    break
        # Domain tier: 0=preferred, 1=neutral, 2=deprioritized
        tier = 0 if preferred else (2 if deprioritized else 1)
        # Tuple: part-number match (0=yes), PDF (0=yes), domain tier, domain name
        return (0 if has_pn else 1, 0 if is_pdf else 1, tier, domain)

    deduped.sort(key=rank)
    return deduped


def _save_cache(corpus: ResearchCorpus) -> Path:
    key = hashlib.sha256(corpus.prompt.encode("utf-8")).hexdigest()[:16]
    path = CACHE_DIR / f"corpus_{key}_{int(corpus.generated_at)}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(corpus.to_dict(), f, indent=2, ensure_ascii=False)
    return path


def corpus_to_markdown(corpus: ResearchCorpus) -> str:
    """
    Flatten a corpus into a single, clean flowing markdown report --
    text and its related images interleaved per source (no separate
    galleries), one document you can read top to bottom.
    """
    ok_sources = [s for s in corpus.sources if not s.error]
    failed_sources = [s for s in corpus.sources if s.error]
    total_images = sum(len(s.images) for s in ok_sources)
    title = corpus.prompt.strip().splitlines()[0][:90]

    lines = [
        f"# 🔩 PCB Design Research: {title}\n",
        f"**🧠 Full prompt:** {corpus.prompt}  ",
        f"**🔍 Search queries used ({len(corpus.queries)}):** {', '.join(corpus.queries)}  ",
        f"**📄 Sources scraped:** {len(ok_sources)}  ",
        f"**🖼️ Relevant images found:** {total_images}  ",
        f"**⚠️ Failed fetches:** {len(failed_sources)}  ",
        "\n---\n",
    ]

    if corpus.summary:
        lines.append(corpus.summary.strip() + "\n")
        lines.append("\n---\n")
    elif corpus.summary_error:
        lines.append(f"> ⚠️ AI summary unavailable: {corpus.summary_error}\n")
        lines.append("\n---\n")

    for i, s in enumerate(ok_sources, 1):
        lines.append(f"## {i}. {s.title}\n")
        lines.append(f"**🔗 URL:** [{s.url}]({s.url})  ")
        lines.append(f"**🔍 Matched query:** *{s.query}*  ")
        lines.append(f"**🖼️ Images in this section:** {len(s.images)}  \n")
        # Text and images are already interleaved in s.markdown, in the
        # order they appeared on the source page -- this is the "text,
        # then its related image, then text, then image" layout.
        lines.append(s.markdown.strip() if s.markdown.strip() else "*(no relevant content extracted)*")
        lines.append("\n---\n")

    if failed_sources:
        lines.append("## ⚠️ Failed to scrape\n")
        for s in failed_sources:
            lines.append(f"- {s.url} — {s.error}")

    return "\n".join(lines)


# ── DigiKey helpers ───────────────────────────────────────────────────────────

def _digikey_part_from_url(url: str) -> Optional[str]:
    """
    Extract a likely part number from a DigiKey product URL.

    Heuristic: the last path segment that looks like a part number
    (contains a letter followed by a digit, or has a hyphen with letters
    on both sides) is taken.  Purely-numeric segments and common words
    like ``detail``, ``filter``, ``base-product`` are ignored.
    """
    parsed = urlparse(url)
    segments = [s for s in parsed.path.split("/") if s and s not in (
        "en", "products", "detail", "base-product", "filter", "search"
    )]
    # Walk backwards: the part number is usually near the end of the path
    for seg in reversed(segments):
        # Must have at least one letter AND one digit
        if re.search(r"[A-Za-z]", seg) and re.search(r"\d", seg):
            return seg.upper()
    return None


def _digikey_part_from_query(query: str,
                              context_terms: Optional[List[str]] = None) -> Optional[str]:
    """
    Fallback: use the search query or context terms as the part number.
    """
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9\-\.]+", query or "")
    for w in words:
        if re.search(r"[A-Za-z]", w) and re.search(r"\d", w):
            return w.upper()

    if context_terms:
        for t in context_terms:
            if re.search(r"[A-Za-z]", t) and re.search(r"\d", t):
                return t.upper()
    return None
