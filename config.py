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