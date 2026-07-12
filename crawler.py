
import time
import re
import json
from urllib.parse import urljoin
 
from playwright.sync_api import sync_playwright
 
from config import (
    HEADLESS,
    PAGE_TIMEOUT,
    USER_AGENT,
    SUBREDDIT_SCROLLS,
    POST_SCROLLS,
    SCROLL_PAUSE,
    MAX_COMMENT_EXPAND_CLICKS
)
from utils import clean_text
 
 
class RedditCrawler:
    BASE_URL = "https://www.reddit.com"
 
    def __init__(self, logger):
        self.logger = logger
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
 
    # ---------------------------------------------------------
    # Browser lifecycle
    # ---------------------------------------------------------
    def start(self):
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(headless=HEADLESS)
        self.context = self.browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1440, "height": 2200}
        )
        self.page = self.context.new_page()
        self.page.set_default_timeout(PAGE_TIMEOUT)
 
    def close(self):
        try:
            if self.browser:
                self.browser.close()
        finally:
            if self.playwright:
                self.playwright.stop()
 
    # ---------------------------------------------------------
    # Shared helpers
    # ---------------------------------------------------------
    def _dismiss_popups(self):
        try:
            self.page.keyboard.press("Escape")
        except Exception:
            pass
 
        selectors = [
            "button:has-text('Close')",
            "button:has-text('Not now')",
            "button[aria-label='Close']"
        ]
        for selector in selectors:
            try:
                loc = self.page.locator(selector)
                if loc.count() > 0:
                    loc.first.click(timeout=2000)
                    self.logger.info(f"Popup dismissed with selector: {selector}")
            except Exception:
                pass
 
    def _scroll_page(self, scrolls, pause):
        for i in range(scrolls):
            self.logger.info(f"Scrolling page... {i + 1}/{scrolls}")
            self.page.mouse.wheel(0, 6000)
            time.sleep(pause)
 
    # ---------------------------------------------------------
    # Feed page (unchanged - this part already works for you)
    # ---------------------------------------------------------
    def open_subreddit_and_collect_post_links(self, subreddit_url: str, max_posts: int):
        self.logger.info(f"Opening subreddit: {subreddit_url}")
        self.page.goto(subreddit_url, wait_until="domcontentloaded")
        time.sleep(4)
        self._dismiss_popups()
        self._scroll_page(SUBREDDIT_SCROLLS, SCROLL_PAUSE)
        time.sleep(2)
 
        html = self.page.content()
 
        links = []
        seen = set()
 
        anchors = self.page.locator("a[href*='/comments/']")
        count = anchors.count()
 
        for i in range(count):
            try:
                href = anchors.nth(i).get_attribute("href")
                if not href:
                    continue
 
                full_url = urljoin(self.BASE_URL, href)
                full_url = full_url.split("?")[0].split("#")[0].rstrip("/")
 
                if "/comments/" not in full_url:
                    continue
 
                m = re.search(r"(https://www\.reddit\.com/r/[^/]+/comments/[^/]+/[^/]+)", full_url)
                if m:
                    full_url = m.group(1)
 
                if full_url in seen:
                    continue
 
                seen.add(full_url)
                links.append(full_url)
 
                if len(links) >= max_posts:
                    break
            except Exception:
                continue
 
        self.logger.info(f"Collected {len(links)} post links from subreddit.")
        return html, links
 
    # ---------------------------------------------------------
    # Post page - visited only for a screenshot now.
    # Comment/meta extraction no longer depends on this page's DOM.
    # ---------------------------------------------------------
    def open_post_and_expand_comments(self, post_url: str):
        self.logger.info(f"Opening post: {post_url}")
        self.page.goto(post_url, wait_until="domcontentloaded")
        time.sleep(4)
        self._dismiss_popups()
        self._scroll_page(2, 1)
        time.sleep(1)
 
    # ---------------------------------------------------------
    # JSON API extraction (primary, reliable method)
    # ---------------------------------------------------------
    def fetch_post_and_comments(self, post_url: str):
        """
        Fetches the post + full comment tree from Reddit's public JSON
        endpoint (<post_url>/.json). This avoids relying on the site's
        front-end HTML/CSS structure entirely, which changes often and
        is what was causing '0 comments extracted' and ad cards showing
        up as comments.
 
        Returns (meta_dict, raw_comments_list). raw_comments_list items
        have: author, body, score, depth - same shape the rest of the
        pipeline (extractor.py) already expects.
        """
        json_url = post_url.rstrip("/") + "/.json"
 
        try:
            response = self.context.request.get(
                json_url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json"
                },
                timeout=PAGE_TIMEOUT
            )
        except Exception as e:
            self.logger.warning(f"JSON request failed for {post_url}: {e}")
            return self._fallback_dom_extraction(post_url)
 
        if not response.ok:
            self.logger.warning(
                f"JSON endpoint returned status {response.status} for {post_url}. "
                f"Falling back to DOM extraction."
            )
            return self._fallback_dom_extraction(post_url)
 
        try:
            data = response.json()
        except Exception as e:
            self.logger.warning(f"Could not parse JSON for {post_url}: {e}")
            return self._fallback_dom_extraction(post_url)
 
        try:
            meta = self._parse_meta_from_json(data, post_url)
            raw_comments = self._parse_comments_from_json(data)
        except Exception as e:
            self.logger.warning(f"Error parsing Reddit JSON for {post_url}: {e}")
            return self._fallback_dom_extraction(post_url)
 
        self.logger.info(
            f"JSON extraction succeeded: {len(raw_comments)} comment(s) found."
        )
        return meta, raw_comments
 
    def _parse_meta_from_json(self, data, post_url):
        try:
            post_data = data[0]["data"]["children"][0]["data"]
        except (IndexError, KeyError, TypeError):
            post_data = {}
 
        title = clean_text(post_data.get("title") or "")
        author = post_data.get("author")
        subreddit = post_data.get("subreddit_name_prefixed") or post_data.get("subreddit")
        upvotes = post_data.get("score")
        selftext = post_data.get("selftext") or ""
        content = clean_text(selftext) if selftext else None
 
        return {
            "title": title or None,
            "author": author,
            "subreddit": subreddit,
            "upvotes": str(upvotes) if upvotes is not None else None,
            "content": content,
            "post_url": post_url
        }
 
    def _parse_comments_from_json(self, data):
        raw_comments = []
 
        try:
            comment_listing = data[1]["data"]["children"]
        except (IndexError, KeyError, TypeError):
            return raw_comments
 
        self._flatten_comment_children(comment_listing, depth=0, acc=raw_comments)
        return raw_comments
 
    def _flatten_comment_children(self, children, depth, acc):
        for child in children:
            kind = child.get("kind")
            item = child.get("data", {})
 
            if kind == "more":
                # Reddit collapses deep/long threads behind a "more" node.
                # Skipping these (no extra request per node) keeps the
                # scraper fast; increase depth handling here later if you
                # need every last collapsed reply.
                continue
 
            if kind != "t1":
                continue
 
            body = item.get("body") or ""
            author = item.get("author") or ""
 
            if body and body not in ("[deleted]", "[removed]") and author != "[deleted]":
                score = item.get("ups")
                acc.append({
                    "author": author,
                    "body": clean_text(body),
                    "score": str(score) if score is not None else None,
                    "depth": depth
                })
 
            replies = item.get("replies")
            if isinstance(replies, dict):
                nested_children = replies.get("data", {}).get("children", [])
                if nested_children:
                    self._flatten_comment_children(nested_children, depth + 1, acc)
 
    # ---------------------------------------------------------
    # DOM fallback (used only if the JSON endpoint fails, e.g.
    # a private/quarantined subreddit or a network block)
    # ---------------------------------------------------------
    def _fallback_dom_extraction(self, post_url):
        self.logger.info("Attempting DOM-based fallback extraction...")
        meta = self.extract_post_meta_from_dom(post_url)
        raw_comments = self.extract_comments_from_dom()
        return meta, raw_comments
 
    def extract_post_meta_from_dom(self, post_url: str):
        title = None
        author = None
        subreddit = None
        upvotes = None
        content = None
 
        try:
            title_loc = self.page.locator("h1").first
            title = clean_text(title_loc.inner_text())
        except Exception:
            pass
 
        try:
            user_links = self.page.locator("a[href^='/user/']")
            if user_links.count() > 0:
                author = clean_text(user_links.first.inner_text())
        except Exception:
            pass
 
        try:
            sub_links = self.page.locator("a[href^='/r/']")
            for i in range(sub_links.count()):
                href = sub_links.nth(i).get_attribute("href") or ""
                txt = clean_text(sub_links.nth(i).inner_text())
                if txt and "/comments/" not in href and href.startswith("/r/"):
                    subreddit = txt
                    break
        except Exception:
            pass
 
        body_chunks = []
        seen = set()
        try:
            paragraphs = self.page.locator("p")
            para_count = min(paragraphs.count(), 120)
            for i in range(para_count):
                txt = clean_text(paragraphs.nth(i).inner_text())
                if not txt or len(txt) < 20:
                    continue
                if txt in seen:
                    continue
                seen.add(txt)
                body_chunks.append(txt)
                if len(body_chunks) >= 12:
                    break
        except Exception:
            pass
 
        if body_chunks:
            content = "\n".join(body_chunks)
 
        try:
            page_text = clean_text(self.page.locator("body").inner_text())
            match = re.search(r"(\d+)\s+votes?", page_text.lower())
            if match:
                upvotes = match.group(1)
        except Exception:
            pass
 
        return {
            "title": title,
            "author": author,
            "subreddit": subreddit,
            "upvotes": upvotes,
            "content": content,
            "post_url": post_url
        }
 
    def extract_comments_from_dom(self):
        raw_comments = []
 
        ad_wrapper_selectors = [
            "shreddit-ad-post",
            "shreddit-async-loader[bundlename*='ad']",
            "[data-testid='ad-post-unit']",
        ]
 
        selectors = [
            "shreddit-comment",
            "faceplate-comment",
            "[data-testid='comment']",
            "div[id^='t1_']",
            "div[thingid^='t1_']",
        ]
 
        for selector in selectors:
            try:
                nodes = self.page.locator(selector)
                count = nodes.count()
                self.logger.info(f"Selector '{selector}' matched {count} node(s).")
 
                if count > 0:
                    raw_comments.extend(
                        self._extract_comment_nodes(nodes, ad_wrapper_selectors)
                    )
 
            except Exception as e:
                self.logger.warning(f"Selector '{selector}' failed: {e}")
                continue
 
        unique = []
        seen = set()
 
        for comment in raw_comments:
            key = (
                comment["author"],
                comment["body"],
                comment["depth"]
            )
 
            if key in seen:
                continue
 
            seen.add(key)
            unique.append(comment)
 
        self.logger.info(f"DOM fallback extracted {len(unique)} comments.")
 
        return unique
 
    def _is_ad_node(self, node):
        try:
            for attr in ("promoted", "is-ad", "data-promoted"):
                val = node.get_attribute(attr)
                if val is not None and val.lower() != "false":
                    return True
        except Exception:
            pass
 
        try:
            class_attr = (node.get_attribute("class") or "").lower()
            if "promoted" in class_attr or "sponsored" in class_attr or "ad-" in class_attr:
                return True
        except Exception:
            pass
 
        try:
            if node.locator(
                "xpath=ancestor::*[contains(@class,'promoted') or contains(@class,'sponsored')]"
            ).count() > 0:
                return True
        except Exception:
            pass
 
        return False
 
    def _extract_comment_nodes(self, nodes, ad_wrapper_selectors=None, limit=300):
        comments = []
        total = min(nodes.count(), limit)
        ad_wrapper_selectors = ad_wrapper_selectors or []
 
        for i in range(total):
            try:
                node = nodes.nth(i)
 
                if self._is_ad_node(node):
                    continue
 
                text = clean_text(node.inner_text())
                if not text or len(text) < 20 or len(text) > 6000:
                    continue
 
                lowered = text.lower()
                if lowered.startswith(("promoted", "sponsored", "advertisement")):
                    continue
                if re.search(r"\b(promoted|sponsored)\b", lowered[:60]):
                    continue
 
                author = None
                try:
                    user_link = node.locator("a[href^='/user/']").first
                    if user_link.count() > 0:
                        author = clean_text(user_link.inner_text())
                except Exception:
                    pass
 
                if not author:
                    continue
 
                score = None
                score_match = re.search(r"(\d+)\s+points?", text.lower())
                if score_match:
                    score = score_match.group(1)
 
                depth = self._infer_depth_from_locator(node)
 
                comments.append({
                    "author": author,
                    "body": text,
                    "score": score,
                    "depth": depth
                })
 
            except Exception:
                continue
 
        return comments
 
    def _infer_depth_from_locator(self, node):
        try:
            aria = node.get_attribute("aria-level")
            if aria and str(aria).isdigit():
                return max(int(aria) - 1, 0)
        except Exception:
            pass
 
        try:
            dd = node.get_attribute("data-depth")
            if dd and str(dd).isdigit():
                return int(dd)
        except Exception:
            pass
 
        try:
            style = node.get_attribute("style") or ""
            match = re.search(r"margin-left:\s*(\d+)px", style)
            if match:
                return int(match.group(1)) // 20
        except Exception:
            pass
 
        return 0