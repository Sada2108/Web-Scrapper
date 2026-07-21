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
from extractor import RedditExtractor
from exporter import RedditExporter
from utils import setup_logger, print_post_terminal
 
 
def main():
    parser = argparse.ArgumentParser(
        description="Reddit Scraper V2 - Subreddit -> Top N Posts -> Full Threads"
    )
 
    # Parse arguments (kept for compatibility, but not used)
    parser.parse_args()
 
    print("=" * 60)
    print("            Reddit Scraper V2")
    print("=" * 60)
 
    # Ask user for subreddit URL
    user_url = input(
        f"\nEnter the Reddit Subreddit URL\n(Default: {DEFAULT_SUBREDDIT_URL})\n> "
    ).strip()
 
    if not user_url:
        user_url = DEFAULT_SUBREDDIT_URL
 
    # Ask user for maximum posts
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
    logger.info("=== Reddit Scraper V2 Started ===")
 
    crawler = RedditCrawler(logger)
    extractor = RedditExtractor(logger)
    exporter = RedditExporter(logger)
 
    all_posts = []
 
    try:
        crawler.start()
 
        print("\nOpening subreddit...")
        print(f"URL       : {user_url}")
        print(f"Max Posts : {max_posts}\n")
 
        # -----------------------------------------------------
        # 1) Collect post links
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
 
        print(f"\nCollected {len(post_links)} post links.\n")
 
        # -----------------------------------------------------
        # 2) Visit every post
        # -----------------------------------------------------
        for idx, post_url in enumerate(post_links, start=1):
 
            print("=" * 80)
            print(f"Scraping Post {idx}/{len(post_links)}")
            print("=" * 80)
 
            logger.info(f"Scraping post {idx}/{len(post_links)}: {post_url}")
 
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
 
            post = extractor.build_post_from_dom(meta, raw_comments)
            all_posts.append(post)
 
            print_post_terminal(post, index=idx)
 
        # -----------------------------------------------------
        # 3) Export
        # -----------------------------------------------------
        exporter.export_threads_json(all_posts, JSON_OUTPUT)
        exporter.export_threads_csv(all_posts, CSV_OUTPUT)
 
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
        logger.info("=== Reddit Scraper V2 Finished ===")
 
 
if __name__ == "__main__":
    main()