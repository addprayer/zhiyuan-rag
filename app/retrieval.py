"""Hybrid retrieval: cosine vector recall + BM25 lexical ranking."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Any

from .db import Database
from .embeddings import EmbeddingError, build_embedder
from .text_utils import snippet, tokenize
from .vector_store import VectorStore


@dataclass
class RetrievalHit:
    chunk_id: int
    document_id: str
    document_name: str
    page: int | None
    section: str | None
    chunk_index: int
    content: str
    vector_score: float
    lexical_score: float
    score: float

    def as_source(self, index: int, query: str) -> dict[str, Any]:
        return {
            "index": index,
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "document_name": self.document_name,
            "page": self.page,
            "section": self.section,
            "chunk_index": self.chunk_index,
            "vector_score": round(self.vector_score, 4),
            "lexical_score": round(self.lexical_score, 4),
            "score": round(self.score, 4),
            "snippet": snippet(self.content, query),
            "content": self.content,
        }


def _bm25_scores(
    rows: list[Any], query: str, k1: float = 1.5, b: float = 0.75
) -> dict[int, float]:
    if not rows:
        return {}
    query_tokens = set(tokenize(query))
    if not query_tokens:
        return {}
    tokenized: list[list[str]] = []
    df: Counter[str] = Counter()
    for row in rows:
        tokens = tokenize(row["content"])
        tokenized.append(tokens)
        for token in set(tokens):
            df[token] += 1
    total = len(rows)
    avg_len = sum(len(tokens) for tokens in tokenized) / max(1, total)
    scores: dict[int, float] = {}
    for row, tokens in zip(rows, tokenized):
        tf = Counter(tokens)
        length = len(tokens)
        score = 0.0
        for token in query_tokens:
            freq = tf.get(token, 0)
            if not freq:
                continue
            idf = math.log(1 + (total - df[token] + 0.5) / (df[token] + 0.5))
            denominator = freq + k1 * (1 - b + b * length / max(avg_len, 1e-6))
            score += idf * (freq * (k1 + 1)) / denominator
        if score > 0:
            scores[int(row["chunk_id"])] = score
    return scores


def retrieve(
    db: Database,
    settings: dict[str, Any],
    kb_id: str,
    query: str,
    top_k: int = 5,
) -> tuple[list[RetrievalHit], dict[str, Any]]:
    rows = db.all(
        """
        SELECT c.id AS chunk_id, c.doc_id, c.content, c.page, c.section,
               c.chunk_index, d.name AS document_name
        FROM chunks c
        JOIN documents d ON d.id = c.doc_id
        WHERE c.kb_id = ?
        """,
        (kb_id,),
    )
    if not rows:
        return [], {"vector_available": True, "vector_error": None, "candidates": 0}

    meta = {int(row["chunk_id"]): row for row in rows}
    vector_error: str | None = None
    vector_scores: dict[int, float] = {}
    try:
        embedder = build_embedder(settings)
        query_vector = embedder.embed_query(query)
        vector_hits = VectorStore(db).search(kb_id, query_vector, limit=120)
        vector_scores = {hit.chunk_id: max(0.0, hit.score) for hit in vector_hits}
    except EmbeddingError as exc:
        vector_error = str(exc)
    except Exception as exc:  # network / provider failure should not break search
        vector_error = f"向量检索不可用：{exc}"

    lexical_raw = _bm25_scores(rows, query)
    lexical_max = max(lexical_raw.values(), default=0.0)
    lexical_scores = {
        chunk_id: value / lexical_max if lexical_max > 0 else 0.0
        for chunk_id, value in lexical_raw.items()
    }

    alpha = float(settings.get("hybrid_alpha", 0.65))
    if vector_error:
        alpha = 0.0
    alpha = min(1.0, max(0.0, alpha))

    hits: list[RetrievalHit] = []
    for chunk_id, row in meta.items():
        vector_score = vector_scores.get(chunk_id, 0.0)
        lexical_score = lexical_scores.get(chunk_id, 0.0)
        score = alpha * vector_score + (1 - alpha) * lexical_score
        if score < 0.06:
            continue
        hits.append(
            RetrievalHit(
                chunk_id=chunk_id,
                document_id=str(row["doc_id"]),
                document_name=str(row["document_name"]),
                page=row["page"],
                section=row["section"],
                chunk_index=int(row["chunk_index"]),
                content=str(row["content"]),
                vector_score=vector_score,
                lexical_score=lexical_score,
                score=score,
            )
        )

    hits.sort(key=lambda hit: hit.score, reverse=True)

    # Keep the context varied: at most three chunks from one document.
    selected: list[RetrievalHit] = []
    per_doc: Counter[str] = Counter()
    seen_content: set[str] = set()
    for hit in hits:
        fingerprint = hit.content[:80].strip()
        if fingerprint in seen_content:
            continue
        if per_doc[hit.document_id] >= 3:
            continue
        selected.append(hit)
        per_doc[hit.document_id] += 1
        seen_content.add(fingerprint)
        if len(selected) >= top_k:
            break

    return selected, {
        "vector_available": vector_error is None,
        "vector_error": vector_error,
        "candidates": len(rows),
        "alpha": alpha,
    }
