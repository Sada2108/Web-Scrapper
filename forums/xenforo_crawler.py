"""
XenForoCrawler

Generic crawler for any forum running on the XenForo platform.
XenForo powers a lot of the EE/electronics forum world, so one crawler
here covers many sites just by changing the URL:

    - https://forum.allaboutcircuits.com/
    - https://electricalengineering.forum/
    - https://www.electronics-lab.com/forums/
    - forums.mikeholt.com, eevblog forums, etc. (also XenForo)

XenForo markup is very stable across sites/versions, which is what makes
this generic approach viable (the same way kicad_crawler.py works for
any Discourse forum):

    thread list item : .structItem
    thread title link: .structItem-title a
    post/reply       : article.message  (data-author="<username>")
    post body        : .message-body .bbWrapper
    post date        : time[datetime]
    like/react count : .reactionsBar-link, .likesSummary

If a given XenForo site has custom-themed markup, only the SELECTORS
dict below should need adjusting - everything else is layout-agnostic.
"""

import re
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright

from config import (
    HEADLESS,
    PAGE_TIMEOUT,
    USER_AGENT
)


class XenForoCrawler:

    def __init__(self, logger):
        self.logger = logger
        self.playwright = None
        self.browser = None
        self.page = None
        self._popups_dismissed = False
        self.base_url = None  # set on first navigation, used to resolve relative links

    # --------------------------------------------------
    # Browser lifecycle
    # --------------------------------------------------
    def start(self):
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(headless=HEADLESS)
        self.page = self.browser.new_page(user_agent=USER_AGENT)
        self.page.set_default_timeout(PAGE_TIMEOUT)

    def close(self):
        try:
            if self.browser:
                self.browser.close()
        except Exception:
            pass

        try:
            if self.playwright:
                self.playwright.stop()
        except Exception:
            pass

    # --------------------------------------------------
    # Navigation helpers
    # --------------------------------------------------
    def _safe_goto(self, url):
        self.logger.info(f"Opening {url}")

        if not self.base_url:
            m = re.match(r"(https?://[^/]+)", url)
            if m:
                self.base_url = m.group(1)

        self.page.goto(url, wait_until="domcontentloaded", timeout=30000)

        try:
            self.page.wait_for_load_state("networkidle", timeout=1500)
        except Exception:
            pass

        self._check_bot_challenge(url)

    def _check_bot_challenge(self, url):
        """
        Some XenForo sites sit behind Cloudflare (Turnstile/"Attention
        Required" challenge pages). We don't attempt to solve or bypass
        these - that's a deliberate anti-bot measure, not a bug to work
        around. Detect it and fail loudly instead of silently returning
        zero links/posts, which is what happened before this check
        existed and just looked like a scraper bug.
        """
        title = ""
        try:
            title = self.page.title()
        except Exception:
            pass

        challenge_titles = ("just a moment", "attention required")
        if any(t in title.lower() for t in challenge_titles):
            raise Exception(
                f"'{url}' is behind a Cloudflare bot-detection challenge "
                f"(page title: {title!r}). This forum can't be scraped "
                f"automatically - it needs to be browsed manually."
            )

    def _scroll_page(self, max_iterations=8):
        last_height = 0
        for _ in range(max_iterations):
            self.page.mouse.wheel(0, 5000)
            self.page.wait_for_timeout(300)
            try:
                height = self.page.evaluate("document.body.scrollHeight")
            except Exception:
                break
            if height == last_height:
                break
            last_height = height

    def _dismiss_popups(self):
        if self._popups_dismissed:
            return

        buttons = [
            "button:has-text('Accept')",
            "button:has-text('I Agree')",
            "button:has-text('Agree')",
            "button:has-text('Close')",
            "a.p-navgroup-link--close",  # XenForo cookie/consent widget close
        ]

        for button in buttons:
            locator = self.page.locator(button).first
            try:
                if locator.count() and locator.is_visible():
                    locator.click(timeout=800)
            except Exception:
                pass

        self._popups_dismissed = True

    # --------------------------------------------------
    # Feed page: either a forum/subforum listing threads,
    # or a single thread URL (in which case we just scrape that one).
    # --------------------------------------------------
    def open_subreddit_and_collect_post_links(self, subreddit_url, max_posts):
        """
        Named to match the shared crawler interface used by app.py/main.py
        (RedditCrawler / KiCadCrawler use the same method name).

        If `subreddit_url` already points at a thread (contains '/threads/'),
        it's treated as a single post to scrape - same behavior as
        KiCadCrawler when given a direct thread link.

        Otherwise it's treated as a forum/subforum index and thread links
        are collected from it, up to max_posts.
        """
        self.logger.info(f"Opening XenForo page: {subreddit_url}")

        self._safe_goto(subreddit_url)
        self._dismiss_popups()
        self._scroll_page()

        html = self.page.content()

        if "/threads/" in subreddit_url:
            self.logger.info("URL already points at a single thread.")
            return html, [subreddit_url]

        links = []
        seen = set()

        anchors = self.page.locator("div.structItem-title a[href*='/threads/']")
        count = anchors.count()

        for i in range(count):
            try:
                href = anchors.nth(i).get_attribute("href")
                if not href:
                    continue

                full_url = urljoin(self.base_url + "/", href)
                full_url = full_url.split("?")[0].split("#")[0].rstrip("/")

                if "/threads/" not in full_url:
                    continue

                if full_url in seen:
                    continue

                seen.add(full_url)
                links.append(full_url)

                if len(links) >= max_posts:
                    break
            except Exception:
                continue

        self.logger.info(f"Collected {len(links)} thread link(s) from forum index.")
        return html, links

    # --------------------------------------------------
    # Thread page: load + expand
    # --------------------------------------------------
    def open_post_and_expand_comments(self, post_url):
        self.logger.info(f"Opening thread: {post_url}")

        self._safe_goto(post_url)
        self._dismiss_popups()
        self._scroll_page()

        # XenForo paginates long threads instead of infinite-scrolling
        # ("Show more" style buttons aren't standard here), so we just
        # make sure the first page is fully rendered. Pagination beyond
        # page 1 is intentionally not followed to keep behavior aligned
        # with the KiCad crawler (first page of a thread only).

    # --------------------------------------------------
    # Extract thread + replies
    # --------------------------------------------------
    def fetch_post_and_comments(self, post_url):
        self.logger.info("Extracting XenForo thread...")

        title = self._safe_text(self.page.locator("h1.p-title-value"))
        if not title:
            title = self._safe_text(self.page.locator("h1"))

        forum_name = self._safe_text(
            self.page.locator("a.p-breadcrumbs-link").last, "XenForo Forum"
        )

        posts = self.page.locator("article.message")
        count = posts.count()

        if count == 0:
            raise Exception("No posts found on this thread.")

        first = posts.nth(0)

        author = self._get_author(first)
        content = self._get_post_content(first)

        meta = {
            "title": title,
            "author": author,
            "subreddit": forum_name,
            "upvotes": "",
            "post_url": post_url,
            "content": content,
        }

        raw_comments = []

        for i in range(1, count):
            post = posts.nth(i)

            reply_author = self._get_author(post)
            reply_body = self._get_post_content(post)
            likes = self._get_like_count(post)
            date = self._safe_attribute(post.locator("time"), "datetime")

            if not reply_body:
                continue

            raw_comments.append({
                "author": reply_author,
                "body": reply_body,
                "score": likes,
                "depth": 0,
                "date": date
            })

        self.logger.info(f"Extracted {len(raw_comments)} replies.")

        return meta, raw_comments

    # --------------------------------------------------
    # Helper functions
    # --------------------------------------------------
    def _safe_text(self, locator, default=""):
        try:
            if locator.count() == 0:
                return default
            return locator.first.inner_text(timeout=500).strip()
        except Exception:
            return default

    def _safe_attribute(self, locator, attribute, default=""):
        try:
            if locator.count() == 0:
                return default
            value = locator.first.get_attribute(attribute, timeout=500)
            return value if value else default
        except Exception:
            return default

    def _get_post_content(self, post):
        selectors = [
            ".message-body .bbWrapper",
            ".bbWrapper",
            ".message-content",
        ]

        for selector in selectors:
            try:
                locator = post.locator(selector)
                if locator.count():
                    text = locator.first.inner_text().strip()
                    if text:
                        return text
            except Exception:
                pass

        return ""

    def _get_author(self, post):
        # XenForo articles carry the poster's username as a data attribute,
        # which is more reliable than scraping the visible name link.
        try:
            attr = post.get_attribute("data-author")
            if attr:
                return attr
        except Exception:
            pass

        selectors = [
            ".message-name .username",
            "a.username",
            ".message-userDetails .username",
        ]

        for selector in selectors:
            try:
                locator = post.locator(selector)
                if locator.count():
                    return locator.first.inner_text().strip()
            except Exception:
                pass

        return "Unknown"

    def _get_like_count(self, post):
        selectors = [
            ".reactionsBar-link",
            ".likesSummary",
            ".message-reactionsBar",
        ]

        for selector in selectors:
            try:
                locator = post.locator(selector)
                if locator.count():
                    text = locator.first.inner_text().strip()
                    if text:
                        return text
            except Exception:
                pass

        return ""