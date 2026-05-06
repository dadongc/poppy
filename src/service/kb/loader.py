from __future__ import annotations


def load_content(content: bytes, mime_type: str) -> tuple[str, dict | None]:
    if mime_type in ("text/markdown", "text/x-markdown"):
        return load_markdown(content)
    if mime_type.startswith("text/") or mime_type == "application/json":
        return load_plaintext(content)
    if mime_type == "text/html":
        return load_html(content)
    if mime_type == "application/pdf":
        return load_pdf(content)
    text = content.decode("utf-8", errors="replace")
    return text, None


def load_markdown(content: bytes) -> tuple[str, dict]:
    text = content.decode("utf-8", errors="replace")
    sections = _parse_markdown_headings(text)
    return text, {"type": "markdown", "sections": sections}


def load_plaintext(content: bytes) -> tuple[str, dict | None]:
    text = content.decode("utf-8", errors="replace")
    return text, None


def load_html(content: bytes) -> tuple[str, dict | None]:
    try:
        from trafilatura import extract  # noqa: PLC0415

        html = content.decode("utf-8", errors="replace")
        extracted = extract(html)
        if extracted:
            return extracted, None
    except ImportError:
        pass
    text = content.decode("utf-8", errors="replace")
    clean = _strip_html(text)
    return clean, None


def load_pdf(content: bytes) -> tuple[str, dict | None]:
    return f"[pdf, size={len(content)} bytes]", None


def _parse_markdown_headings(text: str) -> list[dict]:
    import re

    sections: list[dict] = []
    current_pos = 0
    heading_pattern = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
    heading_stack: list[tuple[int, str]] = []

    for m in heading_pattern.finditer(text):
        level = len(m.group(1))
        title = m.group(2).strip()

        if m.start() > current_pos:
            body = text[current_pos : m.start()]
            if body.strip():
                sections.append(
                    {
                        "text": body,
                        "char_start": current_pos,
                        "char_end": m.start(),
                        "heading_path": [h[1] for h in heading_stack],
                    }
                )

        heading_stack = [h for h in heading_stack if h[0] < level]
        heading_stack.append((level, title))
        current_pos = m.start()

    # last section
    if current_pos < len(text):
        body = text[current_pos:]
        if body.strip():
            sections.append(
                {
                    "text": body,
                    "char_start": current_pos,
                    "char_end": len(text),
                    "heading_path": [h[1] for h in heading_stack],
                }
            )

    return sections


def _strip_html(html: str) -> str:
    import re

    clean = re.sub(r"<[^>]+>", " ", html)
    clean = re.sub(r"\s+", " ", clean)
    return clean.strip()
