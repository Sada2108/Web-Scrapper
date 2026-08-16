from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class CommentData:
    author: Optional[str] = None
    body: Optional[str] = None
    score: Optional[str] = None
    depth: int = 0
    replies: List["CommentData"] = field(default_factory=list)

    def to_dict(self):
        return {
            "author": self.author,
            "body": self.body,
            "score": self.score,
            "depth": self.depth,
            "replies": [r.to_dict() for r in self.replies]
        }


@dataclass
class PostData:
    title: Optional[str] = None
    author: Optional[str] = None
    subreddit: Optional[str] = None
    upvotes: Optional[str] = None
    comments_count: Optional[str] = None
    post_url: Optional[str] = None
    content: Optional[str] = None
    scraped_from: Optional[str] = None
    comments: List[CommentData] = field(default_factory=list)

    def to_dict(self):
        return {
            "title": self.title,
            "author": self.author,
            "subreddit": self.subreddit,
            "upvotes": self.upvotes,
            "comments_count": self.comments_count,
            "post_url": self.post_url,
            "content": self.content,
            "scraped_from": self.scraped_from,
            "comments": [c.to_dict() for c in self.comments]
        }