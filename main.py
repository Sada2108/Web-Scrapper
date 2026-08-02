import argparse

from config import (
    DEFAULT_SUBREDDIT_URL,
    JSON_OUTPUT,
    CSV_OUTPUT,
    FEED_HTML_OUTPUT,
    LOG_FILE,
    SCREENSHOT_DIR,
    MAX_POSTS
)

from crawler import RedditCrawler
from kicad_crawler import KiCadCrawler

from extractor import RedditExtractor
from exporter import RedditExporter
from utils import setup_logger, print_post_terminal


def main():
    parser = argparse.ArgumentParser(
        description="Electronics Forum Scraper"
    )

    parser.parse_args()

    print("=" * 60)
    print("      Electronics Forum Scraper")
    print("=" * 60)

    # -----------------------------------------------------
    # Forum Selection
    # -----------------------------------------------------
    print("\nChoose Forum")
    print("1. Reddit")
    print("2. KiCad")

    choice = input("\nEnter your choice: ").strip()

    if choice == "1":

        print("\nReddit Selected")

        user_url = input(
            f"\nEnter Reddit Subreddit URL\n(Default: {DEFAULT_SUBREDDIT_URL})\n> "
        ).strip()

        if not user_url:
            user_url = DEFAULT_SUBREDDIT_URL

    elif choice == "2":

        print("\nKiCad Selected")

        user_url = input(
            "\nEnter KiCad Thread URL\n(Default: https://forum.kicad.info)\n> "
        ).strip()

        if not user_url:
            user_url = "https://forum.kicad.info"

    else:
        print("Invalid Choice")
        return

    # -----------------------------------------------------
    # Maximum Posts
    # -----------------------------------------------------
    while True:

        user_posts = input(
            f"\nEnter maximum number of posts to scrape (Default: {MAX_POSTS})\n> "
        ).strip()

        if user_posts == "":
            max_posts = MAX_POSTS
            break

        try:
            max_posts = int(user_posts)

            if max_posts <= 0:
                print("Please enter a number greater than 0.")
                continue

            break

        except ValueError:
            print("Please enter a valid integer.")

    logger = setup_logger(LOG_FILE)
    logger.info("=== Electronics Forum Scraper Started ===")

    # -----------------------------------------------------
    # Choose crawler
    # -----------------------------------------------------
    if choice == "1":
        crawler = RedditCrawler(logger)
    else:
        crawler = KiCadCrawler(logger)

    extractor = RedditExtractor(logger)
    exporter = RedditExporter(logger)

    all_posts = []

    try:

        crawler.start()

        print("\nOpening forum...")
        print(f"URL       : {user_url}")
        print(f"Max Posts : {max_posts}")

        # -----------------------------------------------------
        # Collect thread links
        # -----------------------------------------------------
        feed_html, post_links = crawler.open_subreddit_and_collect_post_links(
            subreddit_url=user_url,
            max_posts=max_posts
        )

        with open(FEED_HTML_OUTPUT, "w", encoding="utf-8") as f:
            f.write(feed_html)

        if not post_links:
            print("No post links found.")
            return

        print(f"\nCollected {len(post_links)} thread links.\n")

        # -----------------------------------------------------
        # Visit each thread
        # -----------------------------------------------------
        for idx, post_url in enumerate(post_links, start=1):

            print("=" * 80)
            print(f"Scraping Thread {idx}/{len(post_links)}")
            print("=" * 80)

            logger.info(
                f"Scraping thread {idx}/{len(post_links)} : {post_url}"
            )

            crawler.open_post_and_expand_comments(post_url)

            screenshot_path = SCREENSHOT_DIR / f"post_{idx}.png"

            try:
                crawler.page.screenshot(
                    path=str(screenshot_path),
                    full_page=True
                )
            except Exception:
                pass

            meta, raw_comments = crawler.fetch_post_and_comments(post_url)

            post = extractor.build_post_from_dom(
                meta,
                raw_comments
            )

            all_posts.append(post)

            print_post_terminal(post, index=idx)

        # -----------------------------------------------------
        # Export
        # -----------------------------------------------------
        exporter.export_threads_json(
            all_posts,
            JSON_OUTPUT
        )

        exporter.export_threads_csv(
            all_posts,
            CSV_OUTPUT
        )

        print("\n" + "=" * 60)
        print("Scraping Completed Successfully!")
        print("=" * 60)

        print(f"\nJSON File       : {JSON_OUTPUT}")
        print(f"CSV File        : {CSV_OUTPUT}")
        print(f"Feed HTML       : {FEED_HTML_OUTPUT}")
        print(f"Screenshots Dir : {SCREENSHOT_DIR}")

    except Exception as e:

        logger.exception(f"Scraper failed: {e}")
        print(f"\nError: {e}")

    finally:

        crawler.close()

        logger.info("=== Electronics Forum Scraper Finished ===")


if __name__ == "__main__":
    main()