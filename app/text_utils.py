"""Text normalisation and tokenisation shared by retrieval and embeddings."""

from __future__ import annotations

import re

_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9_+#.\-]*")

_STOP_CHARS = set(
    "的了是在和与及或等中對对为為从從到把被就都也而其这這那有无無不要可以我你他她它们們"
    "吗嗎呢吧啊呀么麼着著过過地得之"
)
_STOP_BIGRAMS = {
    "多少",
    "怎么",
    "如何",
    "什么",
    "哪些",
    "可以",
    "需要",
    "是否",
    "以及",
    "一个",
    "我们",
    "你们",
    "他们",
    "这个",
    "那个",
    "如果",
    "因为",
    "所以",
    "但是",
    "并且",
    "进行",
    "相关",
}
_EN_STOP = {
    "the",
    "a",
    "an",
    "is",
    "are",
    "was",
    "were",
    "of",
    "to",
    "in",
    "on",
    "at",
    "and",
    "or",
    "for",
    "with",
    "how",
    "what",
    "which",
    "why",
    "do",
    "does",
    "did",
    "this",
    "that",
    "be",
    "by",
    "as",
    "it",
    "from",
}


def normalize_text(text: str) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u3000", " ").replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def tokenize(text: str) -> list[str]:
    """Chinese unigrams + bigrams, plus Latin/number words.

    Bigrams give phrase-level signal for Chinese without a dictionary; unigrams
    keep recall for short queries. This tokenizer is used by both BM25 and the
    offline hashed embedder, so no model download is required.
    """
    text = (text or "").lower()
    tokens: list[str] = []
    for match in _WORD.finditer(text):
        word = match.group(0)
        if word in _EN_STOP:
            continue
        if len(word) > 1 or word.isdigit():
            tokens.append(word)

    for match in _CJK_RUN.finditer(text):
        run = match.group(0)
        for char in run:
            if char not in _STOP_CHARS:
                tokens.append(char)
        for i in range(len(run) - 1):
            bigram = run[i : i + 2]
            if bigram not in _STOP_BIGRAMS and not all(c in _STOP_CHARS for c in bigram):
                tokens.append(bigram)
    return tokens


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？!?；;])\s*|\n+", text or "")
    return [part.strip() for part in parts if part.strip()]


def clip(text: str, limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def snippet(text: str, query: str, limit: int = 220) -> str:
    """Return a short window around the first meaningful query term."""
    clean = re.sub(r"\s+", " ", text or "").strip()
    if len(clean) <= limit:
        return clean
    candidates = [t for t in tokenize(query) if len(t) > 1]
    pos = -1
    for token in candidates:
        pos = clean.lower().find(token.lower())
        if pos >= 0:
            break
    if pos < 0:
        return clip(clean, limit)
    start = max(0, pos - limit // 4)
    end = min(len(clean), start + limit)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(clean) else ""
    return prefix + clean[start:end].strip() + suffix
