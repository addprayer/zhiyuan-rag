"""A compact persistent vector index stored in SQLite.

The project deliberately avoids a native vector database dependency so it runs
on a clean Python install. Vectors are float32 BLOBs in the ``chunks`` table and
searched with a cosine scan. For tens of thousands of chunks this is fast enough
for an internal knowledge base; the ``VectorStore`` boundary keeps the rest of
the app independent from that choice.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .db import Database


@dataclass
class VectorHit:
    chunk_id: int
    score: float


def pack_vector(vector: np.ndarray) -> bytes:
    return np.asarray(vector, dtype="<f4").tobytes()


def unpack_vector(blob: bytes, dim: int) -> np.ndarray:
    return np.frombuffer(blob, dtype="<f4", count=dim).copy()


class VectorStore:
    def __init__(self, db: Database):
        self.db = db

    def search(
        self, kb_id: str, query_vector: np.ndarray, limit: int = 80
    ) -> list[VectorHit]:
        query = np.asarray(query_vector, dtype=np.float32)
        query_norm = float(np.linalg.norm(query))
        if query_norm == 0:
            return []
        rows = self.db.all(
            "SELECT id, embedding, dim FROM chunks WHERE kb_id = ?", (kb_id,)
        )
        hits: list[VectorHit] = []
        for row in rows:
            dim = int(row["dim"])
            if dim != len(query):
                continue
            vector = unpack_vector(row["embedding"], dim)
            norm = float(np.linalg.norm(vector))
            if norm == 0:
                continue
            score = float(np.dot(query, vector) / (query_norm * norm))
            if score > 0:
                hits.append(VectorHit(chunk_id=int(row["id"]), score=score))
        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits[:limit]

    def stats(self, kb_id: str) -> dict[str, int]:
        row = self.db.one(
            "SELECT COUNT(*) AS chunks, COALESCE(MAX(dim), 0) AS dim "
            "FROM chunks WHERE kb_id = ?",
            (kb_id,),
        )
        return {
            "chunks": int(row["chunks"]) if row else 0,
            "dim": int(row["dim"]) if row else 0,
        }
