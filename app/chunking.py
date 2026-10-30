"""Heading-aware, sentence-friendly chunking."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .text_utils import normalize_text, split_sentences


@dataclass
class Segment:
    text: str
    page: int | None = None
    section: str | None = None


@dataclass
class Chunk:
    content: str
    page: int | None
    section: str | None
    index: int = 0


def _split_long_block(block: str, limit: int) -> list[str]:
    sentences = split_sentences(block)
    if not sentences:
        sentences = [block]
    units: list[str] = []
    buffer = ""
    for sentence in sentences:
        if len(sentence) > limit:
            if buffer:
                units.append(buffer)
                buffer = ""
            for start in range(0, len(sentence), limit):
                units.append(sentence[start : start + limit])
            continue
        if buffer and len(buffer) + 1 + len(sentence) > limit:
            units.append(buffer)
            buffer = sentence
        else:
            buffer = f"{buffer}\n{sentence}".strip() if buffer else sentence
    if buffer:
        units.append(buffer)
    return units


def _overlap_tail(text: str, overlap: int) -> str:
    if overlap <= 0 or not text:
        return ""
    tail = text[-overlap:]
    if len(tail) < overlap * 0.55:
        return tail.strip()
    match = re.search(r"[。！？!?；;\n]", tail)
    if match and match.start() < len(tail) * 0.55:
        tail = tail[match.start() + 1 :]
    return tail.strip()


def _chunk_segment(segment: Segment, chunk_size: int, overlap: int) -> list[Chunk]:
    text = normalize_text(segment.text)
    if not text:
        return []
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    units: list[str] = []
    for paragraph in paragraphs:
        if len(paragraph) <= chunk_size:
            units.append(paragraph)
        else:
            units.extend(_split_long_block(paragraph, chunk_size))

    chunks: list[Chunk] = []
    buffer = ""
    for unit in units:
        if buffer and len(buffer) + 2 + len(unit) > chunk_size:
            chunks.append(
                Chunk(buffer.strip(), page=segment.page, section=segment.section)
            )
            tail = _overlap_tail(buffer, overlap)
            if tail and len(tail) + 1 + len(unit) > chunk_size:
                tail = ""
            buffer = f"{tail}\n{unit}".strip() if tail else unit
        else:
            buffer = f"{buffer}\n{unit}".strip() if buffer else unit
    if buffer.strip():
        chunks.append(
            Chunk(buffer.strip(), page=segment.page, section=segment.section)
        )
    return chunks


def chunk_segments(
    segments: list[Segment], chunk_size: int = 520, overlap: int = 80
) -> list[Chunk]:
    chunk_size = max(200, int(chunk_size))
    overlap = max(0, min(int(overlap), chunk_size // 2))
    chunks: list[Chunk] = []
    for segment in segments:
        chunks.extend(_chunk_segment(segment, chunk_size, overlap))
    for index, chunk in enumerate(chunks):
        chunk.index = index
    return chunks
