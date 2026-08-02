"""
Electronics Forum Scraper — Streamlit UI

Run with:
    streamlit run app.py

Wraps the existing RedditCrawler / KiCadCrawler / RedditExtractor / RedditExporter
pipeline (the same one main.py drives) behind a UI. No scraping logic lives here.
"""

import time
import traceback
from pathlib import Path

import streamlit as st

from config import (
    DEFAULT_SUBREDDIT_URL,
    JSON_OUTPUT,
    CSV_OUTPUT,
    FEED_HTML_OUTPUT,
    LOG_FILE,
    SCREENSHOT_DIR,
    MAX_POSTS,
)
from crawler import RedditCrawler
from kicad_crawler import KiCadCrawler
from extractor import RedditExtractor
from exporter import RedditExporter
from utils import setup_logger


# ============================================================
# Page setup
# ============================================================

st.set_page_config(
    page_title="Signal — Forum Scraper",
    page_icon="⌁",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ============================================================
# Visual identity
#
# Palette
#   bg        #0D1526  blueprint navy
#   panel     #16203A  panel navy
#   line      #2A3A5C  hairline / grid
#   copper    #BF7A42  Reddit trace / primary accent
#   teal      #4FD1AE  KiCad trace / success
#   amber     #E8A33D  running / warning state
#   text      #EAF0FB  primary text
#   muted     #7E90AC  secondary text
#
# Type
#   Space Grotesk — display / headers (technical, geometric)
#   Inter          — body
#   JetBrains Mono — data, logs, urls
# ============================================================

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap');

html, body, [class*="css"] {
    font-family: 'Inter', sans-serif;
}

.stApp {
    background:
        radial-gradient(ellipse 80% 50% at 50% -10%, rgba(191,122,66,0.06), transparent),
        linear-gradient(180deg, #0D1526 0%, #0F1A2E 100%);
}

/* faint schematic grid on the main canvas */
[data-testid="stAppViewContainer"] > .main {
    background-image:
        linear-gradient(rgba(255,255,255,0.02) 1px, transparent 1px),
        linear-gradient(90deg, rgba(255,255,255,0.02) 1px, transparent 1px);
    background-size: 28px 28px;
}

h1, h2, h3 {
    font-family: 'Space Grotesk', sans-serif !important;
    letter-spacing: -0.01em;
}

code, .stCode, .stDownloadButton, .mono {
    font-family: 'JetBrains Mono', monospace !important;
}

/* ---- sidebar : control panel ---- */
section[data-testid="stSidebar"] {
    border-right: 1px solid #2A3A5C;
}
section[data-testid="stSidebar"] .block-container {
    padding-top: 2rem;
}
.panel-eyebrow {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.7rem;
    letter-spacing: 0.18em;
    color: #7E90AC;
    text-transform: uppercase;
    margin-bottom: 0.35rem;
}
.panel-title {
    font-family: 'Space Grotesk', sans-serif;
    font-size: 1.15rem;
    font-weight: 700;
    color: #EAF0FB;
    margin-bottom: 1.4rem;
    padding-bottom: 0.9rem;
    border-bottom: 1px solid #2A3A5C;
}

/* ---- buttons ---- */
.stButton > button {
    font-family: 'JetBrains Mono', monospace;
    font-weight: 500;
    letter-spacing: 0.03em;
    border-radius: 4px;
    border: 1px solid #BF7A42;
    transition: all 0.15s ease;
}
.stButton > button:hover {
    border-color: #E8A33D;
    box-shadow: 0 0 0 1px #E8A33D;
}

/* ---- metric cards ---- */
[data-testid="stMetric"] {
    background: #16203A;
    border: 1px solid #2A3A5C;
    border-left: 3px solid #BF7A42;
    border-radius: 6px;
    padding: 0.9rem 1rem;
}
[data-testid="stMetricLabel"] {
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 0.7rem !important;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: #7E90AC !important;
}
[data-testid="stMetricValue"] {
    font-family: 'Space Grotesk', sans-serif !important;
    color: #EAF0FB !important;
}

/* ---- expanders : post cards ---- */
[data-testid="stExpander"] {
    background: #16203A;
    border: 1px solid #2A3A5C !important;
    border-radius: 6px;
    margin-bottom: 0.6rem;
}

/* ---- divider hairline ---- */
hr {
    border-color: #2A3A5C !important;
}

/* ---- status / log block ---- */
[data-testid="stStatusWidget"] {
    background: #10182B;
    border: 1px solid #2A3A5C;
    border-radius: 6px;
}
</style>
"""

st.markdown(CSS, unsafe_allow_html=True)


def hero(source_label: str = "REDDIT + KICAD"):
    """Signature element: two source traces converging on one scrape node."""
    st.markdown(
        f"""
        <div style="margin-bottom: 0.4rem;">
          <svg viewBox="0 0 900 130" width="100%" height="130" preserveAspectRatio="xMidYMid meet">
            <circle cx="90" cy="30" r="4" fill="#BF7A42"/>
            <text x="112" y="35" fill="#BF7A42" font-family="JetBrains Mono" font-size="13" letter-spacing="1">REDDIT</text>
            <path d="M 95 32 C 300 32, 350 65, 430 65" stroke="#BF7A42" stroke-width="1.5" fill="none" opacity="0.7"/>

            <circle cx="90" cy="100" r="4" fill="#4FD1AE"/>
            <text x="112" y="105" fill="#4FD1AE" font-family="JetBrains Mono" font-size="13" letter-spacing="1">KICAD</text>
            <path d="M 95 98 C 300 98, 350 65, 430 65" stroke="#4FD1AE" stroke-width="1.5" fill="none" opacity="0.7"/>

            <circle cx="440" cy="65" r="6" fill="#E8A33D"/>
            <path d="M 446 65 L 620 65" stroke="#E8A33D" stroke-width="1.5" opacity="0.85"/>
            <circle cx="628" cy="65" r="5" fill="#E8A33D"/>
            <text x="648" y="70" fill="#EAF0FB" font-family="Space Grotesk" font-weight="600" font-size="16">SCRAPE OUTPUT</text>
          </svg>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ============================================================
# Session state
# ============================================================

st.session_state.setdefault("posts", [])
st.session_state.setdefault("last_run_meta", None)
st.session_state.setdefault("error", None)


# ============================================================
# Sidebar — control panel
# ============================================================

with st.sidebar:
    st.markdown('<div class="panel-title">⌁ CONTROL PANEL</div>', unsafe_allow_html=True)

    st.markdown('<div class="panel-eyebrow">Source</div>', unsafe_allow_html=True)
    forum = st.radio(
        "Forum",
        ["Reddit", "KiCad"],
        horizontal=True,
        label_visibility="collapsed",
    )

    st.markdown('<div class="panel-eyebrow">Target URL</div>', unsafe_allow_html=True)
    default_url = DEFAULT_SUBREDDIT_URL if forum == "Reddit" else "https://forum.kicad.info"
    target_url = st.text_input(
        "Target URL",
        value=default_url,
        label_visibility="collapsed",
    )

    st.markdown('<div class="panel-eyebrow">Max posts</div>', unsafe_allow_html=True)
    max_posts = st.number_input(
        "Max posts",
        min_value=1,
        value=MAX_POSTS,
        label_visibility="collapsed",
    )

    st.write("")
    run_clicked = st.button("▶  START SCRAPE", use_container_width=True, type="primary")

    st.write("")
    st.markdown('<div class="panel-eyebrow">Outputs</div>', unsafe_allow_html=True)
    st.caption(f"JSON  → `{Path(JSON_OUTPUT).name}`")
    st.caption(f"CSV   → `{Path(CSV_OUTPUT).name}`")
    st.caption(f"Log   → `{Path(LOG_FILE).name}`")


# ============================================================
# Main — hero
# ============================================================

st.markdown("### Electronics Forum Scraper")
st.caption("Pull threads and replies from Reddit or the KiCad forum into a single structured output.")
hero()
st.divider()


# ============================================================
# Helpers to read Post fields regardless of whether Post is a
# dict or an object (adjust here if your models.py differs)
# ============================================================

def field(obj, name, default=""):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def get_comments(post):
    return field(post, "comments", field(post, "raw_comments", []))


# ============================================================
# Run scrape
# ============================================================

if run_clicked:
    st.session_state.posts = []
    st.session_state.error = None
    start_time = time.time()

    logger = setup_logger(LOG_FILE)
    crawler = RedditCrawler(logger) if forum == "Reddit" else KiCadCrawler(logger)
    extractor = RedditExtractor(logger)
    exporter = RedditExporter(logger)

    all_posts = []

    with st.status(f"Scraping {forum}…", expanded=True) as status:
        try:
            st.write(f"`{target_url}`")
            crawler.start()

            status.update(label="Collecting thread links…")
            feed_html, post_links = crawler.open_subreddit_and_collect_post_links(
                subreddit_url=target_url,
                max_posts=max_posts,
            )

            with open(FEED_HTML_OUTPUT, "w", encoding="utf-8") as f:
                f.write(feed_html)

            if not post_links:
                status.update(label="No post links found.", state="error")
                st.session_state.error = "No post links found for this URL."
            else:
                st.write(f"Found **{len(post_links)}** thread(s).")
                progress = st.progress(0.0)

                for idx, post_url in enumerate(post_links, start=1):
                    status.update(label=f"Thread {idx}/{len(post_links)}")
                    st.write(f"→ `{post_url}`")

                    crawler.open_post_and_expand_comments(post_url)

                    try:
                        screenshot_path = SCREENSHOT_DIR / f"post_{idx}.png"
                        crawler.page.screenshot(path=str(screenshot_path), full_page=True)
                    except Exception:
                        pass

                    meta, raw_comments = crawler.fetch_post_and_comments(post_url)
                    post = extractor.build_post_from_dom(meta, raw_comments)
                    all_posts.append(post)

                    progress.progress(idx / len(post_links))

                exporter.export_threads_json(all_posts, JSON_OUTPUT)
                exporter.export_threads_csv(all_posts, CSV_OUTPUT)

                elapsed = time.time() - start_time
                status.update(label=f"Done — {len(all_posts)} thread(s) in {elapsed:.1f}s", state="complete")

                st.session_state.posts = all_posts
                st.session_state.last_run_meta = {
                    "forum": forum,
                    "url": target_url,
                    "elapsed": elapsed,
                }

        except Exception as e:
            logger.exception(f"Scraper failed: {e}")
            status.update(label="Scrape failed", state="error")
            st.session_state.error = f"{e}\n\n{traceback.format_exc(limit=3)}"

        finally:
            crawler.close()


if st.session_state.error:
    st.error(st.session_state.error)


# ============================================================
# Results
# ============================================================

posts = st.session_state.posts

if posts:
    meta = st.session_state.last_run_meta or {}
    total_comments = sum(len(get_comments(p)) for p in posts)

    c1, c2, c3 = st.columns(3)
    c1.metric("Threads", len(posts))
    c2.metric("Total replies", total_comments)
    c3.metric("Elapsed", f"{meta.get('elapsed', 0):.1f}s")

    st.write("")

    dcol1, dcol2 = st.columns(2)
    if Path(JSON_OUTPUT).exists():
        dcol1.download_button(
            "⬇ Download JSON",
            data=open(JSON_OUTPUT, "rb").read(),
            file_name=Path(JSON_OUTPUT).name,
            use_container_width=True,
        )
    if Path(CSV_OUTPUT).exists():
        dcol2.download_button(
            "⬇ Download CSV",
            data=open(CSV_OUTPUT, "rb").read(),
            file_name=Path(CSV_OUTPUT).name,
            use_container_width=True,
        )

    st.write("")
    st.markdown('<div class="panel-eyebrow" style="margin-top:0.5rem;">Results</div>', unsafe_allow_html=True)

    for i, post in enumerate(posts, start=1):
        title = field(post, "title", "(untitled)")
        author = field(post, "author", "Unknown")
        content = field(post, "content", "")
        comments = get_comments(post)

        with st.expander(f"{i:02d}  ·  {title}  —  {len(comments)} replies"):
            st.caption(f"by **{author}**")
            if content:
                st.write(content[:800] + ("…" if len(content) > 800 else ""))

            if comments:
                st.write("")
                for c in comments[:15]:
                    c_author = field(c, "author", "Unknown")
                    c_body = field(c, "body", "")
                    st.markdown(
                        f"<div style='padding:0.5rem 0.7rem;margin-bottom:0.35rem;"
                        f"background:#10182B;border-left:2px solid #2A3A5C;border-radius:4px;'>"
                        f"<span style='font-family:JetBrains Mono;font-size:0.75rem;color:#7E90AC;'>{c_author}</span>"
                        f"<div style='margin-top:0.2rem;'>{c_body[:400]}</div></div>",
                        unsafe_allow_html=True,
                    )
                if len(comments) > 15:
                    st.caption(f"+ {len(comments) - 15} more replies (see JSON/CSV export).")
else:
    st.markdown(
        "<div style='padding:2.5rem 0; text-align:center; color:#7E90AC;'>"
        "No results yet — configure a source in the sidebar and start a scrape."
        "</div>",
        unsafe_allow_html=True,
    )
