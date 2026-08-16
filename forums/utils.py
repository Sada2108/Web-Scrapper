import json
import logging
import re
from typing import Optional


def setup_logger(log_file):
    logger = logging.getLogger("reddit_scraper_v2")
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)

        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)

        logger.addHandler(file_handler)
        logger.addHandler(console_handler)

    return logger


def clean_text(text: Optional[str]) -> str:
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text).strip()
    return text


def pretty_json_dump(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def print_separator(char="=", length=110):
    print(char * length)


def print_comment_tree(comment, prefix=""):
    indent = "    " * comment.depth
    print(f"{indent}{prefix}Author : {comment.author}")
    print(f"{indent}{prefix}Score  : {comment.score}")
    print(f"{indent}{prefix}Body   : {comment.body}")
    print()

    for idx, reply in enumerate(comment.replies, start=1):
        print_comment_tree(reply, prefix=f"[Reply {idx}] ")


def print_post_terminal(post, index=None):
    print_separator("=")
    if index is not None:
        print(f"REDDIT POST #{index}")
    else:
        print("REDDIT POST")
    print_separator("=")

    print(f"TITLE         : {post.title}")
    print(f"AUTHOR        : {post.author}")
    print(f"SUBREDDIT     : {post.subreddit}")
    print(f"UPVOTES       : {post.upvotes}")
    print(f"COMMENTS      : {post.comments_count}")
    print(f"POST URL      : {post.post_url}")
    print_separator("-")
    print("POST CONTENT / CONTEXT")
    print_separator("-")
    print(post.content if post.content else "No post content extracted.")
    print_separator("-")
    print("COMMENTS + REPLIES")
    print_separator("-")

    if not post.comments:
        print("No comments extracted.")
        return

    for idx, comment in enumerate(post.comments, start=1):
        print_comment_tree(comment, prefix=f"[Comment {idx}] ")