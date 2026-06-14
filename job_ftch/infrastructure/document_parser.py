"""Document text extraction for resume and job example uploads."""

from __future__ import annotations

import html
import io
import re
from html.parser import HTMLParser


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip_tags: set[str] = {"script", "style", "head"}
        self._in_skip: int = 0

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag.lower() in self._skip_tags:
            self._in_skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self._skip_tags and self._in_skip > 0:
            self._in_skip -= 1

    def handle_data(self, data: str) -> None:
        if self._in_skip == 0:
            stripped = data.strip()
            if stripped:
                self._parts.append(stripped)

    def get_text(self) -> str:
        return "\n".join(self._parts)


def _extract_html(content: bytes) -> str:
    text = content.decode("utf-8", errors="replace")
    text = html.unescape(text)
    extractor = _HTMLTextExtractor()
    extractor.feed(text)
    return extractor.get_text()


def _extract_markdown(content: bytes) -> str:
    text = content.decode("utf-8", errors="replace")
    # Strip markdown syntax: headers, bold, italic, code, links, images
    text = re.sub(r"#{1,6}\s*", "", text)
    text = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", text)
    text = re.sub(r"_{1,3}([^_]+)_{1,3}", r"\1", text)
    text = re.sub(r"`{1,3}[^`]*`{1,3}", "", text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\d+\.\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^>{1,}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_pdf(content: bytes) -> str:
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(content))
        pages = []
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                pages.append(page_text)
        return "\n".join(pages)
    except ImportError:
        return "PDF extraction requires 'pypdf' library (install with: pip install pypdf)."
    except Exception as exc:
        return f"PDF extraction failed: {exc}"


def _extract_docx(content: bytes) -> str:
    try:
        from docx import Document

        doc = Document(io.BytesIO(content))
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    except ImportError:
        return "DOCX extraction requires 'python-docx' library (install with: pip install python-docx)."
    except Exception as exc:
        return f"DOCX extraction failed: {exc}"


def parse_document(content: bytes, filename: str) -> str:
    """Extract plain text from uploaded document bytes.

    Supports: .pdf, .docx, .html, .htm, .md, .txt and plain text fallback.
    """
    name_lower = filename.lower()
    if name_lower.endswith(".pdf"):
        return _extract_pdf(content)
    if name_lower.endswith(".docx"):
        return _extract_docx(content)
    if name_lower.endswith((".html", ".htm")):
        return _extract_html(content)
    if name_lower.endswith(".md"):
        return _extract_markdown(content)
    if name_lower.endswith(".txt"):
        return content.decode("utf-8", errors="replace")
    # Unknown extension — try UTF-8, fallback to cp1251
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return content.decode("cp1251", errors="replace")
