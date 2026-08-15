from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"
SCREENSHOT_DIR = DATA_DIR / "screenshots"

DATA_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)
SCREENSHOT_DIR.mkdir(exist_ok=True)

JSON_OUTPUT = DATA_DIR / "reddit_threads.json"
CSV_OUTPUT = DATA_DIR / "reddit_threads.csv"
FEED_HTML_OUTPUT = DATA_DIR / "feed.html"
LOG_FILE = LOG_DIR / "scraper.log"

HEADLESS = False                 # keep False while debugging
PAGE_TIMEOUT = 90000

SUBREDDIT_SCROLLS = 5
POST_SCROLLS = 10
SCROLL_PAUSE = 1.5

MAX_POSTS = 5
MAX_COMMENT_EXPAND_CLICKS = 30   # higher => tries harder to expand threads

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

DEFAULT_SUBREDDIT_URL = "https://www.reddit.com/r/MachineLearning/"

# ============================================================
# Forum registry
#
# Every entry maps a human-readable forum name to the crawler class
# that handles its platform ("platform") and a sensible default URL.
# To add another forum:
#   - if it runs Discourse  -> platform="discourse", reuse KiCadCrawler
#   - if it runs XenForo    -> platform="xenforo",  reuse XenForoCrawler
#   - otherwise             -> write a new crawler and register it here
# app.py / main.py both read this list, so nothing else needs to change
# to make a new forum selectable.
# ============================================================

FORUMS = {
    "Reddit": {
        "platform": "reddit",
        "default_url": DEFAULT_SUBREDDIT_URL,
    },
    "KiCad Forum": {
        "platform": "discourse",
        "default_url": "https://forum.kicad.info/latest",
    },
    "All About Circuits": {
        "platform": "xenforo",
        "default_url": "https://forum.allaboutcircuits.com/forums/general-electronics-chat.5/",
    },
    "Electrical Engineering Forum": {
        "platform": "xenforo",
        "default_url": "https://electricalengineering.forum/",
    },
    "Electronics-Lab.com (PCB Layout & Design)": {
        "platform": "xenforo",
        "default_url": "https://www.electronics-lab.com/forums/forums/pcb-layout-design-and-manufacture.186/",
    },
    "Other XenForo forum (custom URL)": {
        "platform": "xenforo",
        "default_url": "",
    },
}