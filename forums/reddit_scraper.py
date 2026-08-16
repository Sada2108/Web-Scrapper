from playwright.sync_api import sync_playwright
import json
import time


class RedditScraper:

    def __init__(self, headless=False):
        self.headless = headless

    def launch(self):
        self.playwright = sync_playwright().start()

        self.browser = self.playwright.chromium.launch(
            headless=self.headless
        )

        self.page = self.browser.new_page(
            viewport={"width": 1440, "height": 900}
        )

    def close(self):
        self.browser.close()
        self.playwright.stop()

    # ---------------------------------------------------------

    def open_post(self, url):

        print("Opening Reddit...")

        self.page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=60000
        )

        self.page.wait_for_timeout(5000)

    # ---------------------------------------------------------

    def auto_scroll(self):

        previous = 0

        while True:

            current = self.page.evaluate(
                "document.body.scrollHeight"
            )

            self.page.mouse.wheel(0, 5000)

            self.page.wait_for_timeout(1500)

            new = self.page.evaluate(
                "document.body.scrollHeight"
            )

            if new == previous:
                break

            previous = new

    # ---------------------------------------------------------

    def save_debug(self):

        with open(
                "reddit.html",
                "w",
                encoding="utf8"
        ) as f:

            f.write(self.page.content())

        self.page.screenshot(
            path="reddit.png",
            full_page=True
        )

    # ---------------------------------------------------------

    def get_title(self):

        try:
            return self.page.locator("h1").first.inner_text().strip()
        except:
            return ""

    # ---------------------------------------------------------

    def get_post_body(self):

        selectors = [

            "div[slot='text-body']",

            "div.md",

            "div[data-click-id='text']",

            "shreddit-post p",

            "faceplate-tracker p"

        ]

        for selector in selectors:

            try:

                locator = self.page.locator(selector)

                if locator.count():

                    text = "\n".join(
                        locator.all_inner_texts()
                    ).strip()

                    if text:
                        return text

            except:
                pass

        return ""

    # ---------------------------------------------------------

    def extract_comments(self):

        selectors = [

            "shreddit-comment",

            "faceplate-comment",

            "[data-testid='comment']",

            "article"

        ]

        comments = []

        for selector in selectors:

            locator = self.page.locator(selector)

            if locator.count() == 0:
                continue

            print(f"Using selector: {selector}")

            for i in range(locator.count()):

                try:

                    block = locator.nth(i)

                    text = block.inner_text().strip()

                    if len(text) < 20:
                        continue

                    comments.append({

                        "id": i + 1,

                        "text": text

                    })

                except:
                    pass

            if comments:
                break

        return comments

    # ---------------------------------------------------------

    def scrape(self, url):

        self.launch()

        self.open_post(url)

        self.auto_scroll()

        self.save_debug()

        result = {

            "title": self.get_title(),

            "body": self.get_post_body(),

            "comments": self.extract_comments()

        }

        self.close()

        return result


# =========================================================

if __name__ == "__main__":

    url = input("Paste Reddit URL:\n")

    scraper = RedditScraper()

    data = scraper.scrape(url)

    print("\n" + "=" * 80)
    print("POST TITLE")
    print("=" * 80)
    print(data["title"])

    print("\n" + "=" * 80)
    print("POST BODY")
    print("=" * 80)
    print(data["body"])

    print("\n" + "=" * 80)
    print("COMMENTS")
    print("=" * 80)

    for comment in data["comments"]:

        print(f"\nComment {comment['id']}")
        print("-" * 80)
        print(comment["text"])

    with open(
            "reddit_data.json",
            "w",
            encoding="utf8"
    ) as f:

        json.dump(
            data,
            f,
            indent=4,
            ensure_ascii=False
        )

    print("\nSaved:")
    print("reddit_data.json")
    print("reddit.html")
    print("reddit.png")