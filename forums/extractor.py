
from models import PostData, CommentData
 
 
class RedditExtractor:
    def __init__(self, logger):
        self.logger = logger
 
    def build_post_from_dom(self, meta: dict, raw_comments: list):
        comment_tree = self._build_comment_tree(raw_comments)
 
        post = PostData(
            title=meta.get("title"),
            author=meta.get("author"),
            subreddit=meta.get("subreddit"),
            upvotes=meta.get("upvotes"),
            comments_count=str(self._count_comments(comment_tree)),
            post_url=meta.get("post_url"),
            content=meta.get("content"),
            scraped_from=meta.get("post_url"),
            comments=comment_tree
        )
        return post
 
    def _build_comment_tree(self, raw_comments):
        filtered = [
            item for item in raw_comments
            if not self._looks_like_ad(item)
        ]
 
        skipped = len(raw_comments) - len(filtered)
        if skipped:
            self.logger.info(f"Extractor skipped {skipped} ad-like item(s) before tree build.")
 
        comments = [
            CommentData(
                author=item.get("author"),
                body=item.get("body"),
                score=item.get("score"),
                depth=item.get("depth", 0),
                replies=[]
            )
            for item in filtered
        ]
 
        root_comments = []
        stack = []
 
        for comment in comments:
            while stack and stack[-1].depth >= comment.depth:
                stack.pop()
 
            if not stack:
                root_comments.append(comment)
            else:
                stack[-1].replies.append(comment)
 
            stack.append(comment)
 
        return root_comments
 
    def _looks_like_ad(self, item: dict) -> bool:
        """Second line of defense in case a promoted card slips past the crawler."""
        body = (item.get("body") or "").strip().lower()
        author = (item.get("author") or "").strip().lower()
 
        if not body:
            return True
 
        ad_prefixes = ("promoted", "sponsored", "advertisement")
        if body.startswith(ad_prefixes):
            return True
 
        ad_authors = ("promoted", "sponsored", "ad")
        if author in ad_authors:
            return True
 
        return False
 
    def _count_comments(self, comments):
        total = 0
        for c in comments:
            total += 1
            if c.replies:
                total += self._count_comments(c.replies)
        return total