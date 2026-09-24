from __future__ import annotations

import html
import re

import bleach


ALLOWED_TAGS = ["p", "h1", "h2", "h3", "ul", "ol", "li", "strong", "em", "a", "blockquote", "code", "pre", "br"]
ALLOWED_ATTRIBUTES = {"a": ["href", "title", "rel"]}


def render_markdown(source: str) -> str:
    """Small, safe Markdown subset for the vertical slice; raw HTML is escaped."""
    lines = source.replace("\r\n", "\n").split("\n")
    output: list[str] = []
    paragraph: list[str] = []
    in_list = False

    def format_inline(content: str) -> str:
        content = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", content)
        content = re.sub(r"\*(.+?)\*", r"<em>\1</em>", content)
        content = re.sub(r"`([^`]+)`", r"<code>\1</code>", content)
        # Accept absolute http(s) links and root-relative ("/path") links so the
        # published output matches the admin live preview. bleach.clean still
        # sanitises the final href, and the tight character class keeps a URL
        # from breaking out of the attribute.
        return re.sub(
            r"\[([^]]+)\]\((https?://[^)\s\"'<>]+|/[^)\s\"'<>]*)\)",
            r'<a href="\2" rel="nofollow noopener">\1</a>',
            content,
        )

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            content = " ".join(paragraph).strip()
            output.append(f"<p>{format_inline(content)}</p>")
            paragraph = []

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            output.append("</ul>")
            in_list = False

    for raw in lines:
        line = html.escape(raw.strip(), quote=False)
        if not line:
            flush_paragraph()
            close_list()
            continue
        if line.startswith("### "):
            flush_paragraph(); close_list(); output.append(f"<h3>{line[4:]}</h3>"); continue
        if line.startswith("## "):
            flush_paragraph(); close_list(); output.append(f"<h2>{line[3:]}</h2>"); continue
        if line.startswith("# "):
            flush_paragraph(); close_list(); output.append(f"<h1>{line[2:]}</h1>"); continue
        if line.startswith("- "):
            flush_paragraph()
            if not in_list:
                output.append("<ul>"); in_list = True
            output.append(f"<li>{format_inline(line[2:])}</li>")
            continue
        close_list()
        paragraph.append(line)
    flush_paragraph(); close_list()
    return bleach.clean("".join(output), tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRIBUTES, strip=True)
