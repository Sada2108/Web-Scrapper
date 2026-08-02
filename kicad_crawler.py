from playwright.sync_api import sync_playwright
import re

from config import (
    HEADLESS,
    PAGE_TIMEOUT,
    USER_AGENT
)


class KiCadCrawler:

    def __init__(self, logger):
        self.logger = logger
        self.playwright = None
        self.browser = None
        self.page = None
        self._popups_dismissed = False  # only need to do this once per session

    # --------------------------------------------------

    def start(self):

        self.playwright = sync_playwright().start()

        self.browser = self.playwright.chromium.launch(
            headless=HEADLESS
        )

        self.page = self.browser.new_page(
            user_agent=USER_AGENT
        )

        self.page.set_default_timeout(PAGE_TIMEOUT)

    # --------------------------------------------------

    def close(self):

        try:
            self.browser.close()
        except:
            pass

        try:
            self.playwright.stop()
        except:
            pass

    # --------------------------------------------------

    def _safe_goto(self, url):

        self.logger.info(f"Opening {url}")

        self.page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=30000
        )

        # Try to wait for network to settle, but Discourse keeps
        # long-lived connections open (websockets/polling), so
        # "networkidle" can hang until timeout. Cap it short and
        # fall back instead of always paying a flat 2s tax.
        try:
            self.page.wait_for_load_state("networkidle", timeout=1500)
        except Exception:
            pass

    def _scroll_page(self, max_iterations=8):
        """Scroll until content stops growing, instead of always
        doing a fixed number of 1s waits."""

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

    # --------------------------------------------------

    def _dismiss_popups(self):

        # Cookie/consent popups appear once per session, not on
        # every navigation - no need to re-check them every time.
        if self._popups_dismissed:
            return

        buttons = [
            "button:has-text('Accept')",
            "button:has-text('I Agree')",
            "button:has-text('Close')"
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
    # Open a Discourse Thread
    # --------------------------------------------------

    def open_subreddit_and_collect_post_links(
            self,
            subreddit_url,
            max_posts
    ):

        self.logger.info(
            f"Opening Discourse thread: {subreddit_url}"
        )

        self._safe_goto(subreddit_url)

        self._dismiss_popups()

        self._scroll_page()

        html = self.page.content()

        # User already enters a thread URL.
        # So we simply return that one URL.

        links = [subreddit_url]

        self.logger.info(
            "Thread loaded successfully."
        )

        return html, links

    # --------------------------------------------------

    def open_post_and_expand_comments(
            self,
            post_url
    ):

        self.logger.info(
            f"Opening thread: {post_url}"
        )

        self._safe_goto(post_url)

        self._dismiss_popups()

        self._scroll_page()

        # Expand hidden replies if any.
        #
        # THE MAIN BUG: the original click() had no explicit timeout,
        # so it inherited PAGE_TIMEOUT (often 30-60s). On the final
        # iteration - when there's no more "Show more" button - Playwright
        # waits the *entire* default timeout before giving up. That alone
        # could be adding 30-60s to every single thread. Checking
        # count() first (near-instant) and giving click() a short
        # explicit timeout fixes this.

        for _ in range(10):

            button = self.page.locator("button:has-text('Show more')").first

            if button.count() == 0:
                break

            try:
                button.click(timeout=1000)
                self.page.wait_for_timeout(300)
            except Exception:
                break

    # --------------------------------------------------
    # Extract Thread + Replies
    # --------------------------------------------------

    def fetch_post_and_comments(self, post_url):

        self.logger.info("Extracting thread...")

        # No re-navigation here. open_post_and_expand_comments() already
        # loaded this exact URL, dismissed popups, scrolled, and expanded
        # replies. Re-running _safe_goto + _dismiss_popups + _scroll_page
        # here was doing all of that work a second time for nothing -
        # a full reload plus up to another 8s of scrolling per thread.

        # -----------------------------
        # Thread Title
        # -----------------------------

        title = self._safe_text(self.page.locator("h1"))

        # -----------------------------
        # All Posts (Question + Replies)
        # -----------------------------

        posts = self.page.locator("article")

        count = posts.count()

        if count == 0:
            raise Exception("No posts found on this thread.")

        # -----------------------------
        # First Post
        # -----------------------------

        first = posts.nth(0)

        author = self._safe_text(first.locator(".creator a"), "Unknown")
        content = self._safe_text(first.locator(".cooked"))

        meta = {

            "title": title,

            "author": author,

            "subreddit": "KiCad Forum",

            "upvotes": "",

            "post_url": post_url,

            "content": content

        }

        raw_comments = []

        # -----------------------------
        # Remaining Posts = Replies
        # -----------------------------

        for i in range(1, count):

            post = posts.nth(i)

            reply_author = self._safe_text(post.locator(".creator a"), "Unknown")
            reply_body = self._safe_text(post.locator(".cooked"))
            likes = self._safe_text(post.locator(".like-count"))
            date = self._safe_attribute(post.locator("time"), "datetime")

            raw_comments.append({

                "author": reply_author,

                "body": reply_body,

                "score": likes,

                "depth": 0,

                "date": date

            })

        self.logger.info(
            f"Extracted {len(raw_comments)} replies."
        )

        return meta, raw_comments

    # --------------------------------------------------
    # Helper Functions
    # --------------------------------------------------

    def _safe_text(self, locator, default=""):

        try:
            # count() is instant - it doesn't wait around for the
            # element to appear the way inner_text() does. Checking
            # it first means a missing selector fails immediately
            # instead of eating a full default-timeout wait.
            if locator.count() == 0:
                return default

            return locator.first.inner_text(timeout=500).strip()
        except Exception:
            return default

    # --------------------------------------------------

    def _safe_attribute(self, locator, attribute, default=""):

        try:
            if locator.count() == 0:
                return default

            value = locator.first.get_attribute(attribute, timeout=500)

            if value:
                return value

            return default

        except Exception:
            return default

    # --------------------------------------------------

    def _get_post_content(self, post):

        selectors = [
            ".cooked",
            ".regular",
            ".post",
            ".topic-body"
        ]

        for selector in selectors:

            try:
                locator = post.locator(selector)

                if locator.count():
                    text = locator.first.inner_text().strip()

                    if text:
                        return text

            except:
                pass

        return ""

    # --------------------------------------------------

    def _get_author(self, post):

        selectors = [
            ".creator a",
            ".username",
            ".names .username",
            "a[data-user-card]"
        ]

        for selector in selectors:

            try:
                locator = post.locator(selector)

                if locator.count():
                    return locator.first.inner_text().strip()

            except:
                pass

        return "Unknown"

    # --------------------------------------------------

    def _get_like_count(self, post):

        selectors = [
            ".like-count",
            ".actions .like-count",
            ".post-action-menu__like-count"
        ]

        for selector in selectors:

            try:
                locator = post.locator(selector)

                if locator.count():
                    return locator.first.inner_text().strip()

            except:
                pass

        return ""
