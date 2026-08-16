# Reddit-Scrapper — multi-forum work: status

Last updated: 2026-08-16. Current HEAD: `c75c2ab` ("Fix multi-forum crawlers: Discourse author
selector, Cloudflare detection, deps").

## Background

The scraper was generalized from Reddit-only to multi-forum (Reddit / Discourse / XenForo) via
`config.FORUMS` + `crawler_factory.build_crawler()`. That generalization (`app.py`, `config.py`,
`main.py` wiring, plus the original `kicad_crawler.py` and `xenforo_crawler.py`) was written on
Aug 11 but never actually run — no log entries existed for it until this session. This session's
job was to check whether it actually works, then fix what didn't.

## What's done (all verified with live runs against real forums, not just read)

### 1. KiCad / Discourse crawler (`kicad_crawler.py`) — fixed, verified
- OP author selector `.creator a` matched nothing in current Discourse markup. Fixed to
  `.topic-meta-data .names a`.
- Bigger bug found while testing: Discourse virtualizes the post stream — replies not currently
  scrolled into view render as empty "cloaked" `<article>` placeholders, so their body/author came
  back empty and got silently dropped (the `if not reply_body: continue` guard ate them). Fixed by
  calling `post.scroll_into_view_if_needed()` + waiting for `.cooked` to attach before reading each
  reply.
- Verified end-to-end against `https://forum.kicad.info/t/the-unconventional-sot89-footprint/71165`:
  4/4 real replies extracted with correct authors (`vitya`, `retiredfeline`, `paulvdh`, `jmk`) and
  content. Before the fix this thread returned ~1 reply with author `Unknown`.
- **Known remaining gap (not fixed):** Discourse system/moderation posts (e.g. "Closed on ...")
  use a different markup block (`.small-action-desc` / `.small-action-contents a`) than normal
  posts, so those still extract as `Unknown` author. Low priority — it's metadata noise, not a
  real reply being lost.

### 2. Cloudflare / XenForo (`xenforo_crawler.py`, `kicad_crawler.py`) — bounded fix, not a bypass
Explicit decision: did not attempt to defeat Cloudflare Turnstile (no stealth plugins, no
challenge-solving, no fingerprint spoofing). That's the site's anti-bot measure working as
intended, not a scraper bug to route around.

What was actually done: added `_check_bot_challenge()` to both crawlers' `_safe_goto()`. It checks
the page title for `"just a moment"` / `"attention required"` and raises a clear exception
(`"'<url>' is behind a Cloudflare bot-detection challenge ... can't be scraped automatically"`)
instead of letting the crawl silently continue and return zero links/posts.

Verified live state of the three XenForo forums in `config.FORUMS`:
- **All About Circuits** (`forum.allaboutcircuits.com`) — blocked immediately, first request.
- **Electrical Engineering Forum** (`electricalengineering.forum`) — blocked immediately, first
  request ("Attention Required" page).
- **Electronics-Lab.com** — partial: the forum-index/listing page loads fine and real thread links
  are collected (confirmed 3 real thread URLs). But navigating into an actual thread page trips
  the Cloudflare challenge, so `fetch_post_and_comments` now cleanly raises the bot-detection error
  instead of the old opaque `"No posts found on this thread."`.

**Net effect: none of the 3 configured XenForo example forums currently produce a full scrape.**
The fix's value is that failure is now loud and explains itself, instead of looking like a
scraper/selector bug (which is what it looked like before — 0 links, no explanation).

### 3. `.venv` (local environment, not part of git — `.venv` carries its own `.gitignore`)
- `.venv` had no `pip` at all. Bootstrapped via `python -m ensurepip --upgrade`.
- Installed `requirements.txt` (playwright, beautifulsoup4, lxml, pandas) into `.venv`.
- Installed the Chromium browser binary (`playwright install chromium`) — verified with an actual
  headless launch (`browser.version` → `151.0.7922.34`).
- `streamlit` was missing from `requirements.txt` even though `app.py` hard-requires it (was only
  present on the system Python, not tracked as a project dependency). Added it to
  `requirements.txt` and installed it into `.venv`.
- To run anything in this repo now: use `.venv/bin/python`, not system Python.

### Commit
`c75c2ab` — includes the fixes above plus the Aug-11 multi-forum wiring (`app.py`, `config.py`,
`main.py`, `crawler_factory.py`) that was already sitting uncommitted before this session started.
7 files changed: `app.py`, `config.py`, `crawler_factory.py` (new), `kicad_crawler.py`, `main.py`,
`requirements.txt`, `xenforo_crawler.py` (new).

## Remaining / open work

1. **XenForo forums are still not scrapable in practice** (Cloudflare). If real scraping of these
   sites is wanted, the options are outside what this session did: an official API/RSS feed if the
   forum offers one, a commercial residential-proxy + real-browser scraping service, or manual
   collection. Worth explicitly deciding with the user which of these (if any) is acceptable before
   investing more time in XenForo.
2. **Discourse system-post author extraction** (`.small-action-desc` markup) is still unhandled —
   cosmetic, low priority.
3. **No automated test suite exists in this repo.** All verification this session was ad hoc
   throwaway scripts run against live sites, not saved as reusable tests. Worth considering adding
   a small regression test (e.g. a saved HTML fixture per platform) so selector breakage like the
   `.creator a` bug gets caught without a live run.
4. **Repo hygiene, not addressed (out of scope this session):** no `.gitignore` at the repo root —
   `__pycache__/`, `logs/scraper.log`, and `data/feed.html` are tracked and show as dirty on every
   run. `logs/scraper.log` and `data/feed.html` diffs seen this session predate this session (from
   an Aug 3 run) and were left alone rather than bundled into the commit above.
5. **`config.HEADLESS = False` is still the repo default** (intentional, for interactive/debug
   use per the existing comment in `config.py`). All verification this session overrode it to
   `True` in throwaway scripts only — the repo itself wasn't changed.
