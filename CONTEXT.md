# Repo context: PCB-Design-Research-Scraper (aka Web-Scrapper)

## What it is
A Firecrawl-powered research scraper for electronics/PCB design. You enter a
circuit-design prompt (e.g. "picoammeter 10 fA–10 µA") and it produces a
`ResearchCorpus`: relevant text blocks, schematic/circuit images, source links,
and PDF figures — preserving original reading order (text + images interleaved).

Git repo: `https://github.com/Sada2108/Web-Scrapper` (was `Web-Scrapper-`).
Branch: `main`. Remote `origin` already points at the new URL.

## Pipeline
```
prompt  ─▶  generate_search_queries()   keyword extraction (part numbers,
                                         circuit types, EE vocabulary)
       ─▶  Firecrawl /search            candidate pages per query
       ─▶  scrape_source()              Firecrawl /scrape, per-domain strategy
       ─▶  extract_interleaved_content() relevant blocks in reading order
                                         (text + images together)
       ─▶  ResearchCorpus               JSON-serializable result set
```

## Files
| File | Purpose | Size |
|---|---|---|
| `scraper.py` | Core engine: queries, search, scrape, relevance scoring, interleaved extraction, Groq gate, cache | 2133 lines |
| `app.py` | Streamlit UI (sidebar API keys, gallery, corpus view) | 320 |
| `test_scraper.py` | Regression tests (unit + 4 live URL suites) | 713 |
| `digikey_api.py` | DigiKey Product Information v4 API client (OAuth2 client_credentials, token refresh, normalized part details) | 237 |
| `pdf_images.py` | PDF figure extraction via PyMuPDF (embedded pass + caption-region render) + table extraction | 345 |
| `check_groq.py` | Standalone Groq API-key tester (reads `GROQ_API_KEY` env var) | 38 |
| `check.py` | Quick Firecrawl connectivity test | 5 |
| `README.md` | Usage docs (clone URL now stale → uses old `Web-Scrapper-`) | |
| `CONTEXT.md` | This file | |
| `requirements.txt` | streamlit, firecrawl-py, python-dotenv, requests, PyMuPDF | |
| `.env.example` | Template: FIRECRAWL_API_KEY (req), GROQ_API_KEY (opt), cache dir | |
| `.env` | Real keys — gitignored, never committed | |

## Key constants in scraper.py
- `PREFERRED_DOMAINS` — trusted EE domains (ti.com, analog.com, eevblog.com…) → ranked up, not a hard filter.
- `DEPRIORITIZED_DOMAINS` — content-marketing/low-quality → down-ranked, still kept.
- `EXCLUDED_DOMAINS` — **hard excluded before ranking**: amazon/ebay/aliexpress/alibaba + youtube.com, youtu.be, youtube-nocookie.com, vimeo.com (video platforms added recently).
- `JUNK_IMAGE_KEYWORDS` — image-url keywords scored down (social icons incl. `youtube`, logos, spinners).
- SE comment expansion via `ClickAction`+`WaitAction`; `_EXPAND_SELECTORS` per-domain.

## Groq gate (summary/reasonableness)
- `load_dotenv()` at import (scraper.py:21) loads `.env`, so `GROQ_API_KEY` is picked up automatically.
- `_groq_reasonableness_batch(items, prompt, api_key)` — one batched API call; on no-key/network error returns all indices (silent skip). Used for SE recency-reserve gating and gallery filtering.
- Note: because the key now lives in `.env`, the test "no key returns all" makes a live call and currently fails.

## Tests
`python3 test_scraper.py` — 4 suites (unit, Stack Exchange, DigiKey, TI.com, Wikipedia) plus gallery/Groq unit checks. **71/72 pass.** The 1 failure (`_groq_reasonableness_batch with no key returns all`) is env-dependent (real key in `.env` → live call), not a code regression.

## Git history (recent, newest first)
- `fff9383` New Updates related to restriction of certain websites
- `5d7cf4f` Some new Updates (Groq migration, key moved to `.env`)
- `2aeeed7` Merge origin/main, keep local data-cleaning fixes
- `162b03a` new updates for data cleaning
- `fd0674d` new updates
- `e77fd37` Merge PR #1 (aditya-feature)
- `9afd503` Initial updates for web scraper
- … DigiKey v4 API, PDF figure extraction, e-commerce blocklist, SE comment expansion …

Contributors: Sada Chouhan (18 commits) + Aditya Srivastava (2 commits: initial release + PR #1 gallery).

## Cache / artifacts
- `cache/` — `corpus_*.json` run outputs + `pdf_figures/` (726 PNGs extracted from datasheets; mostly logos/graphs/pinouts/photos, no clean schematics found).
- Data-cleaning fixes vs upstream live in local commits (not necessarily pushed to GitHub).

## Known limitations (from README)
- DigiKey parametric pages not scrapable via Firecrawl (JS-rendered + network-blocked) — use the v4 API (implemented in `digikey_api.py`).
- Query planning is heuristic regex, not LLM-driven.
- Image relevance is keyword-based; a vision pass (e.g. Gemma) would improve precision.

## Sibling folder: Open_Forge / gemma4-video-harness
`~/Open_Forge/gemma4-video-harness/` — a local harness testing Gemma 4 (`gemma4:e2b` via Ollama on localhost:11434) for reading schematics/PCB images from EE videos. Files: `test_circuit_gemma.py` (Ollama image tester, works), `test_samples.sh`, `setup.sh`, `requirements.txt`, two sample images. Status: capability gate passed on still images (reads R/C/U values, topology; rejects non-schematic figures); **video pipeline not built** — the YouTube-source idea was shelved and YouTube was instead excluded from scraping here. Ollama limitation: `/api/chat` documents `images` but no `audio` field.

## Related folders on disk (home)
- `PCB-Design-Research-Scraper-backup-best/` — backup snapshot of this repo.
- `~/Downloads/sce.png`, `~/Downloads/pcb layout.png` — user's own test images for the Gemma tester (note the space → always quote the path).
- `gemma4-video-harness` (under Open_Forge), plus many other projects (Reddit-Scrapper, intent-parser, circuit-assistant-code, etc.).

## Gotchas
- Tilde `~` does not expand inside double quotes — use full `/Users/sadachouhan/...` paths.
- `.env` holds FIRECRAWL/GROQ/DigiKey keys; never commit it.
- README clone URL is stale (`Web-Scrapper-`), tests mention old wording in places.
