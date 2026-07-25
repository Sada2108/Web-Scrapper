"""
pdf_images.py
-------------
Extract figures/images from PDF datasheets using PyMuPDF (fitz).

Two strategies:
  1. Embedded pass:  pull real embedded bitmaps via page.get_images()
  2. Caption-region: find "Figure N" captions and render the region above each one

Heuristic note on the caption-region pass:
  We scan text blocks for /fig(?:ure)?\\.?\\s*\\d+/i, get the bounding box, and
  render a cropped pixmap from (caption_top - 40% page height) to caption_top,
  clamped to page bounds.  This is a reasonable heuristic for datasheets where
  figure captions sit directly below the corresponding graph, but it is NOT
  pixel-perfect — it may over-crop (include unrelated text above the figure)
  or under-crop (if the figure extends further up than 40 % of the page).
"""

import hashlib
import os
import re
from typing import List, Optional, Tuple

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None


class PdfFigure:
    """One extracted figure from a PDF."""

    def __init__(
        self,
        image_bytes: bytes,
        page_num: int,
        method: str,
        caption: str = "",
        bbox: Optional[Tuple[float, float, float, float]] = None,
        context_text: str = "",
    ):
        self.image_bytes = image_bytes
        self.page_num = page_num
        self.method = method  # "embedded" or "rendered_region"
        self.caption = caption
        self.bbox = bbox
        self.context_text = context_text

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.image_bytes).hexdigest()[:16]


def extract_pdf_figures(pdf_bytes: bytes) -> List[PdfFigure]:
    """Run both extraction passes and return a deduplicated list of figures."""
    if fitz is None:
        raise ImportError("PyMuPDF (fitz) required: pip install PyMuPDF")

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    figures: List[PdfFigure] = []
    seen_hashes: set = set()

    for page_num in range(len(doc)):
        page = doc[page_num]
        for fig in _embedded_pass(doc, page, page_num):
            h = fig.content_hash
            if h not in seen_hashes:
                seen_hashes.add(h)
                figures.append(fig)
        for fig in _caption_region_pass(page, page_num, doc):
            h = fig.content_hash
            if h not in seen_hashes:
                seen_hashes.add(h)
                figures.append(fig)

    doc.close()
    return figures


# -- embedded pass -----------------------------------------------------------

def _embedded_pass(doc, page, page_num: int) -> List[PdfFigure]:
    """Pull real embedded bitmap images via ``page.get_images(full=True)``."""
    result: List[PdfFigure] = []
    image_list = page.get_images(full=True)
    seen_xrefs: set = set()

    for img_info in image_list:
        xref = img_info[0]
        if xref in seen_xrefs:
            continue
        seen_xrefs.add(xref)

        try:
            raw = doc.extract_image(xref)
            # PyMuPDF < 1.26 used dict with "image" key; 1.26+ changed API.
            if isinstance(raw, dict):
                img_bytes = raw["image"]
            else:
                img_bytes = raw
            result.append(
                PdfFigure(
                    image_bytes=img_bytes,
                    page_num=page_num,
                    method="embedded",
                    caption=f"Embedded image (xref={xref})",
                )
            )
        except Exception:
            continue

    return result


# -- caption-region pass -----------------------------------------------------

def _caption_region_pass(page, page_num: int, doc) -> List[PdfFigure]:
    """
    Find "Figure N" captions and render the page region above each one.

    Heuristic: for each caption we crop from ``max(0, caption_top - 40% page
    height)`` to ``caption_top``.  This works well for typical datasheets
    where the graph sits immediately above its label, but is not guaranteed
    to capture every figure cleanly.

    Each region is checked for content type before rendering:
    - Regions with embedded images in the clip are accepted (already captured
      by the embedded pass, but rendered for completeness).
    - Regions with vector drawings (lines, curves, filled areas) and moderate
      text density are accepted (vector-only figures).
    - Regions with high word count (>=100) are rejected as running text UNLESS
      they contain meaningful visual content (embedded image, 2+ large
      drawings, or a single drawing covering >8% of the clip area).
    """
    # Thresholds for rejecting a region as running text rather than a figure:
    # real figure regions typically have <100 words in the 40%-height crop;
    # TOC listings have 100+ words of running text with no figures nearby.
    _MAX_TEXT_WORDS = 100

    result: List[PdfFigure] = []
    blocks = page.get_text("dict").get("blocks", [])

    page_height = page.rect.height
    page_width = page.rect.width

    captions: List[Tuple[float, str, str]] = []  # (y0, caption_text, context_text)

    for bi, block in enumerate(blocks):
        if block.get("type") != 0:  # 0 = text block
            continue
        text = _block_text(block)
        if re.search(r"fig(?:ure)?\.?\s*\d+", text, re.IGNORECASE):
            bbox = block.get("bbox")
            if bbox:
                # Capture up to ~150 chars of body text immediately above
                # the caption for richer relevance scoring downstream.
                ctx_parts: List[str] = []
                chars_needed = 150
                for j in range(bi - 1, -1, -1):
                    prev_block = blocks[j]
                    if prev_block.get("type") != 0:
                        continue
                    prev_text = _block_text(prev_block).strip()
                    if not prev_text:
                        continue
                    ctx_parts.append(prev_text)
                    chars_needed -= len(prev_text)
                    if chars_needed <= 0:
                        break
                context_text = " ".join(reversed(ctx_parts))[:150]
                captions.append((bbox[1], text.strip(), context_text))

    # Pre-compute page drawings and image-block info once (avoids re-calling
    # get_drawings / get_text for each caption on the same page).
    page_drawings = page.get_drawings()
    page_text_dict = page.get_text("dict")

    for caption_top, caption_text, context_text in captions:
        # Region extends upward by ~40% of page height from the caption baseline
        region_top = max(0, caption_top - 0.40 * page_height)
        # Include the caption line itself plus a small tail for context
        region_bottom = min(page_height, caption_top + page_height * 0.02)
        clip = fitz.Rect(0, region_top, page_width, region_bottom)

        # -- content check ----------------------------------------------------
        # Count embedded image blocks that overlap the clip region.
        image_count = 0
        for block in page_text_dict.get("blocks", []):
            if block.get("type") != 1:  # 1 = image block
                continue
            bbox = block.get("bbox")
            if bbox:
                img_rect = fitz.Rect(*bbox)
                if clip.intersects(img_rect):
                    image_count += 1

        # Count vector drawings (lines, paths, filled regions) that overlap
        # the clip region.  Track both count and largest single drawing area
        # fraction so we can distinguish a real figure from a stray rule.
        clip_area = clip.width * clip.height
        large_drawing_count = 0
        max_drawing_frac = 0.0
        for d in page_drawings:
            d_rect = d.get("rect")
            if d_rect and clip.intersects(d_rect):
                inter = d_rect.intersect(clip)
                frac = (inter.width * inter.height) / clip_area if clip_area else 0
                if frac > 0.01:
                    large_drawing_count += 1
                if frac > max_drawing_frac:
                    max_drawing_frac = frac

        # Word count in the clip region (excluding caption text itself).
        words_in_clip = page.get_text("words", clip=clip)
        word_count = len(words_in_clip)

        # Heuristic: if the region is text-heavy (>=100 words), only keep it
        # when there's meaningful visual content — an embedded image, multiple
        # large drawings, or at least one drawing covering a substantial
        # fraction of the clip (>8%).  A single thin rule/underline (~1-2%
        # area) should never override a text-heavy region.
        if word_count >= _MAX_TEXT_WORDS:
            has_meaningful_visual = (
                image_count > 0
                or large_drawing_count >= 2
                or max_drawing_frac > 0.08
            )
            if not has_meaningful_visual:
                continue  # TOC / index text — skip

        # -- render -----------------------------------------------------------
        try:
            pix = page.get_pixmap(clip=clip, dpi=150)
            img_bytes = pix.tobytes("png")
            result.append(
                PdfFigure(
                    image_bytes=img_bytes,
                    page_num=page_num,
                    method="rendered_region",
                    caption=caption_text,
                    bbox=tuple(clip),
                    context_text=context_text,
                )
            )
        except Exception:
            continue

    return result


def _block_text(block: dict) -> str:
    """Concatenate text spans in a text block."""
    parts: List[str] = []
    for line in block.get("lines", []):
        for span in line.get("spans", []):
            parts.append(span.get("text", ""))
    return " ".join(parts)


# -- saving ------------------------------------------------------------------

def save_figures(figures: List[PdfFigure], output_dir: str) -> List[dict]:
    """Save each figure as a PNG file.  Returns metadata dicts."""
    os.makedirs(output_dir, exist_ok=True)
    saved: List[dict] = []

    for fig in figures:
        filename = f"p{fig.page_num+1:02d}_{fig.method}_{fig.content_hash}.png"
        filepath = os.path.join(output_dir, filename)
        with open(filepath, "wb") as f:
            f.write(fig.image_bytes)
        saved.append(
            {
                "filepath": filepath,
                "page_num": fig.page_num,
                "method": fig.method,
                "caption": fig.caption,
                "context_text": fig.context_text,
                "size_bytes": len(fig.image_bytes),
            }
        )

    return saved


# -- table extraction --------------------------------------------------------

def extract_pdf_tables(pdf_bytes: bytes) -> List[dict]:
    """
    Extract tables from a PDF using PyMuPDF's ``page.find_tables()``.

    Returns a list of dicts, one per table:
      {"page_num": int, "markdown": str, "bbox": tuple, "header": list[str]}
    where *markdown* is a clean pipe-table string ready for insertion into
    the scraped content.
    """
    if fitz is None:
        raise ImportError("PyMuPDF (fitz) required: pip install PyMuPDF")

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    results: List[dict] = []

    for page_num in range(len(doc)):
        page = doc[page_num]
        try:
            tabs = page.find_tables()
        except Exception:
            continue
        for tab in tabs:
            try:
                rows = tab.extract()
            except Exception:
                continue
            if not rows or len(rows) < 2:
                continue

            # Build markdown pipe-table
            header = [str(c).strip() if c else "" for c in rows[0]]
            col_count = len(header)
            # Normalize row widths
            normalized_rows = []
            for row in rows:
                cells = [str(c).strip() if c else "" for c in row]
                # Pad or truncate to match header width
                while len(cells) < col_count:
                    cells.append("")
                normalized_rows.append(cells[:col_count])

            lines = []
            lines.append("| " + " | ".join(normalized_rows[0]) + " |")
            lines.append("| " + " | ".join(["---"] * col_count) + " |")
            for row in normalized_rows[1:]:
                lines.append("| " + " | ".join(row) + " |")

            md_table = "\n".join(lines)
            bbox = tuple(tab.bbox) if hasattr(tab, "bbox") else None
            results.append({
                "page_num": page_num,
                "markdown": md_table,
                "bbox": bbox,
                "header": header,
            })

    doc.close()
    return results
