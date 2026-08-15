"""
Maps a FORUMS[...]['platform'] value (see config.py) to the crawler
class that implements it. Adding a new forum on an already-supported
platform (discourse / xenforo) needs zero changes here - just add an
entry to config.FORUMS. Adding a forum on a brand-new platform means
writing a new crawler with the same method interface as the others
(start, close, open_subreddit_and_collect_post_links,
open_post_and_expand_comments, fetch_post_and_comments) and registering
it in PLATFORM_CRAWLERS below.
"""

from crawler import RedditCrawler
from kicad_crawler import KiCadCrawler
from xenforo_crawler import XenForoCrawler

PLATFORM_CRAWLERS = {
    "reddit": RedditCrawler,
    "discourse": KiCadCrawler,
    "xenforo": XenForoCrawler,
}


def build_crawler(platform: str, logger):
    try:
        crawler_cls = PLATFORM_CRAWLERS[platform]
    except KeyError:
        raise ValueError(
            f"No crawler registered for platform '{platform}'. "
            f"Known platforms: {list(PLATFORM_CRAWLERS)}"
        )
    return crawler_cls(logger)