import pandas as pd
from utils import pretty_json_dump


class RedditExporter:
    def __init__(self, logger):
        self.logger = logger

    def export_threads_json(self, posts, json_path):
        pretty_json_dump(json_path, [p.to_dict() for p in posts])
        self.logger.info(f"JSON exported to {json_path}")

    def export_threads_csv(self, posts, csv_path):
        rows = []

        def add_comment_rows(post, comments):
            for c in comments:
                rows.append({
                    "type": "comment",
                    "depth": c.depth,
                    "title": None,
                    "author": c.author,
                    "subreddit": post.subreddit,
                    "upvotes": c.score,
                    "comments_count": None,
                    "post_url": post.post_url,
                    "content": c.body,
                    "scraped_from": post.scraped_from
                })
                if c.replies:
                    add_comment_rows(post, c.replies)

        for post in posts:
            rows.append({
                "type": "post",
                "depth": 0,
                "title": post.title,
                "author": post.author,
                "subreddit": post.subreddit,
                "upvotes": post.upvotes,
                "comments_count": post.comments_count,
                "post_url": post.post_url,
                "content": post.content,
                "scraped_from": post.scraped_from
            })
            add_comment_rows(post, post.comments)

        df = pd.DataFrame(rows)
        df.to_csv(csv_path, index=False, encoding="utf-8-sig")
        self.logger.info(f"CSV exported to {csv_path}")