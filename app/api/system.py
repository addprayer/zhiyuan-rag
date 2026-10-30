"""Health, stats and index metadata."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ..db import Database
from ..vector_store import VectorStore
from .deps import get_db, get_store

router = APIRouter(prefix="/api", tags=["system"])

VERSION = "1.0.0"


@router.get("/health")
def health(request: Request, db: Database = Depends(get_db)) -> dict:
    store = get_store(request)
    return {
        "status": "ok",
        "name": "知源",
        "version": VERSION,
        "mode": store.public()["mode"],
    }


@router.get("/stats")
def stats(db: Database = Depends(get_db), store=Depends(get_store)) -> dict:
    kb_count = db.one("SELECT COUNT(*) AS n FROM kbs")
    doc_count = db.one("SELECT COUNT(*) AS n FROM documents")
    chunk_count = db.one("SELECT COUNT(*) AS n FROM chunks")
    conversation_count = db.one("SELECT COUNT(*) AS n FROM conversations")
    dim_row = db.one("SELECT COALESCE(MAX(dim), 0) AS dim FROM chunks")
    return {
        "kbs": int(kb_count["n"]) if kb_count else 0,
        "documents": int(doc_count["n"]) if doc_count else 0,
        "chunks": int(chunk_count["n"]) if chunk_count else 0,
        "conversations": int(conversation_count["n"]) if conversation_count else 0,
        "dim": int(dim_row["dim"]) if dim_row else 0,
        "mode": store.public()["mode"],
    }


@router.get("/kbs/{kb_id}/index")
def index_stats(kb_id: str, db: Database = Depends(get_db)) -> dict:
    kb = db.one("SELECT id FROM kbs WHERE id = ?", (kb_id,))
    if not kb:
        return {"error": "知识库不存在"}
    return VectorStore(db).stats(kb_id)
