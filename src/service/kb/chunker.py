from __future__ import annotations

import re
from typing import Any


class Chunker:
    def __init__(
        self,
        target_tokens: int = 512,
        overlap: int = 64,
        min_tokens: int = 128,
    ) -> None:
        self._target = target_tokens
        self._overlap = overlap
        self._min = min_tokens

    def chunk(self, text: str, structure: dict | None = None) -> list[dict[str, Any]]:
        if structure and structure.get("type") == "markdown":
            return self._chunk_markdown(text, structure)
        return self._chunk_recursive(text)

    def _chunk_markdown(self, text: str, structure: dict) -> list[dict[str, Any]]:
        sections = structure.get("sections", [])
        if not sections:
            return self._chunk_recursive(text)

        result: list[dict[str, Any]] = []
        for sec in sections:
            sec_text = sec.get("text", "")
            heading_path = sec.get("heading_path", [])

            if not sec_text.strip():
                continue

            sec_tokens = _estimate_tokens(sec_text)
            if sec_tokens <= self._target:
                result.append(
                    {
                        "text": sec_text.strip(),
                        "char_start": sec.get("char_start", 0),
                        "char_end": sec.get("char_end", len(sec_text)),
                        "heading_path": heading_path,
                        "token_count": sec_tokens,
                    }
                )
            else:
                sub_chunks = self._chunk_recursive(sec_text)
                for c in sub_chunks:
                    c["heading_path"] = heading_path
                result.extend(sub_chunks)

        return result

    def _chunk_recursive(self, text: str) -> list[dict[str, Any]]:
        if not text.strip():
            return []

        total_tokens = _estimate_tokens(text)
        if total_tokens <= self._target:
            return [
                {
                    "text": text.strip(),
                    "char_start": 0,
                    "char_end": len(text),
                    "heading_path": [],
                    "token_count": total_tokens,
                }
            ]

        paragraphs = _split_paragraphs(text)
        if len(paragraphs) > 1:
            return self._merge_chunks(
                self._chunk_items(paragraphs, self._target, self._min, self._overlap)
            )

        sentences = _split_sentences(text)
        if len(sentences) > 1:
            return self._merge_chunks(
                self._chunk_items(sentences, self._target, self._min, self._overlap)
            )

        return self._chunk_by_char(text, self._target, self._overlap)

    def _chunk_items(
        self, items: list[str], target_tokens: int, min_tokens: int, overlap: int
    ) -> list[dict[str, Any]]:
        chunks: list[dict[str, Any]] = []
        current_text = ""
        current_start = 0
        pos = 0

        for item in items:
            item_tokens = _estimate_tokens(item)
            current_tokens = _estimate_tokens(current_text)

            if current_tokens + item_tokens <= target_tokens or current_tokens < min_tokens:
                current_text += item
            else:
                if current_text.strip():
                    chunks.append(
                        {
                            "text": current_text.strip(),
                            "char_start": current_start,
                            "char_end": current_start + len(current_text),
                            "heading_path": [],
                            "token_count": current_tokens,
                        }
                    )
                # start new chunk with overlap
                if overlap > 0 and current_text:
                    overlap_text = _get_last_n_tokens(current_text, overlap)
                    current_text = overlap_text + item
                    current_start = pos - len(overlap_text)
                else:
                    current_text = item
                    current_start = pos
            pos += len(item)

        if current_text.strip():
            chunks.append(
                {
                    "text": current_text.strip(),
                    "char_start": current_start,
                    "char_end": current_start + len(current_text),
                    "heading_path": [],
                    "token_count": _estimate_tokens(current_text),
                }
            )
        return chunks

    def _merge_chunks(self, chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return chunks

    def _chunk_by_char(
        self, text: str, target_tokens: int, overlap: int
    ) -> list[dict[str, Any]]:
        chars_per_token = 1.5
        chunk_chars = int(target_tokens * chars_per_token)
        overlap_chars = min(int(overlap * chars_per_token), chunk_chars - 1)
        result: list[dict[str, Any]] = []

        start = 0
        while start < len(text):
            end = min(start + chunk_chars, len(text))
            chunk_text = text[start:end].strip()
            if chunk_text:
                result.append(
                    {
                        "text": chunk_text,
                        "char_start": start,
                        "char_end": end,
                        "heading_path": [],
                        "token_count": _estimate_tokens(chunk_text),
                    }
                )
            if end >= len(text):
                break
            start = end - overlap_chars
        return result


def _estimate_tokens(text: str) -> int:
    return len(text) // 2


def _split_paragraphs(text: str) -> list[str]:
    parts = re.split(r"(\n\n+)", text)
    result: list[str] = []
    for p in parts:
        stripped = p.strip()
        if stripped:
            result.append(p)
    return result if len(result) > 1 else [text]


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"((?<=[.。!！?？\n])\s*)", text)
    result: list[str] = []
    for p in parts:
        stripped = p.strip()
        if stripped:
            result.append(p)
    return result if len(result) > 1 else [text]


def _get_last_n_tokens(text: str, n: int) -> str:
    if not text:
        return ""
    words = text.split()
    if not words:
        return text[-int(n * 2) :]
    keep = min(len(words), n)
    return " ".join(words[-keep:])
