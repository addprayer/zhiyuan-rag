"""Knowledge bases, documents and retrieval preview."""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from ..config import AppConfig, SettingsStore
from ..db import Database, new_id, now_iso
from ..embeddings import signature_for
from ..ingest import (
    MAX_FILE_SIZE,
    SUPPORTED_EXTENSIONS,
    IngestError,
    document_dict,
    ingest_file,
    reindex_kb,
)
from ..retrieval import retrieve
from ..schemas import KBCreate, KBUpdate, SearchRequest
from .deps import get_config, get_db, get_store

router = APIRouter(prefix="/api", tags=["knowledge"])


def _kb_or_404(db: Database, kb_id: str):
    kb = db.one("SELECT * FROM kbs WHERE id = ?", (kb_id,))
    if not kb:
        raise HTTPException(status_code=404, detail="知识库不存在")
    return kb


def _kb_payload(db: Database, kb) -> dict:
    doc = db.one(
        "SELECT COUNT(*) AS n FROM documents WHERE kb_id = ?", (kb["id"],)
    )
    chunk = db.one("SELECT COUNT(*) AS n FROM chunks WHERE kb_id = ?", (kb["id"],))
    return {
        "id": kb["id"],
        "name": kb["name"],
        "description": kb["description"],
        "embedding_signature": kb["embedding_signature"],
        "created_at": kb["created_at"],
        "document_count": int(doc["n"]) if doc else 0,
        "chunk_count": int(chunk["n"]) if chunk else 0,
    }


@router.get("/kbs")
def list_kbs(db: Database = Depends(get_db)) -> dict:
    rows = db.all("SELECT * FROM kbs ORDER BY created_at ASC")
    return {"items": [_kb_payload(db, row) for row in rows]}


@router.post("/kbs", status_code=201)
def create_kb(
    payload: KBCreate,
    db: Database = Depends(get_db),
    store: SettingsStore = Depends(get_store),
) -> dict:
    duplicate = db.one(
        "SELECT id FROM kbs WHERE lower(name) = lower(?)", (payload.name,)
    )
    if duplicate:
        raise HTTPException(status_code=409, detail="已存在同名知识库")
    kb_id = new_id("kb")
    db.execute(
        "INSERT INTO kbs (id, name, description, embedding_signature, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            kb_id,
            payload.name,
            payload.description,
            signature_for(store.get()),
            now_iso(),
        ),
    )
    return _kb_payload(db, _kb_or_404(db, kb_id))


@router.patch("/kbs/{kb_id}")
def update_kb(
    kb_id: str, payload: KBUpdate, db: Database = Depends(get_db)
) -> dict:
    kb = _kb_or_404(db, kb_id)
    name = payload.name.strip() if payload.name is not None else kb["name"]
    description = (
        payload.description.strip()
        if payload.description is not None
        else kb["description"]
    )
    if not name:
        raise HTTPException(status_code=400, detail="知识库名称不能为空")
    duplicate = db.one(
        "SELECT id FROM kbs WHERE lower(name) = lower(?) AND id != ?", (name, kb_id)
    )
    if duplicate:
        raise HTTPException(status_code=409, detail="已存在同名知识库")
    db.execute(
        "UPDATE kbs SET name = ?, description = ? WHERE id = ?",
        (name, description, kb_id),
    )
    return _kb_payload(db, _kb_or_404(db, kb_id))


@router.delete("/kbs/{kb_id}")
def delete_kb(
    kb_id: str,
    db: Database = Depends(get_db),
    config: AppConfig = Depends(get_config),
) -> dict:
    _kb_or_404(db, kb_id)
    db.execute("DELETE FROM kbs WHERE id = ?", (kb_id,))
    upload_dir = config.upload_dir / kb_id
    if upload_dir.exists():
        shutil.rmtree(upload_dir, ignore_errors=True)
    return {"ok": True}


@router.get("/kbs/{kb_id}/documents")
def list_documents(kb_id: str, db: Database = Depends(get_db)) -> dict:
    _kb_or_404(db, kb_id)
    rows = db.all(
        "SELECT * FROM documents WHERE kb_id = ? ORDER BY created_at DESC", (kb_id,)
    )
    return {"items": [document_dict(row) for row in rows]}


def _safe_filename(name: str) -> str:
    name = Path(name or "document").name
    cleaned = "".join(
        char for char in name if char not in '<>:"/\\|?*\x00-\x1f'
    ).strip(" .")
    return cleaned[:120] or "document"


@router.post("/kbs/{kb_id}/documents", status_code=201)
async def upload_document(
    kb_id: str,
    file: UploadFile = File(...),
    db: Database = Depends(get_db),
    config: AppConfig = Depends(get_config),
    store: SettingsStore = Depends(get_store),
) -> dict:
    kb = _kb_or_404(db, kb_id)
    display_name = _safe_filename(file.filename or "document")
    suffix = Path(display_name).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        supported = "、".join(sorted(SUPPORTED_EXTENSIONS))
        raise HTTPException(
            status_code=400, detail=f"不支持该文件类型，请上传 {supported}"
        )

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="文件为空")
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="文件超过 20 MB 限制")

    kb_dir = config.upload_dir / kb_id
    kb_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(display_name).stem[:60] or "file"
    dest = kb_dir / f"{new_id('f')}_{stem}{suffix}"
    dest.write_bytes(content)

    settings = store.get()
    try:
        document = ingest_file(
            db,
            settings,
            kb,
            dest,
            display_name,
            stored_path=str(dest.relative_to(config.base_dir)),
        )
    except IngestError as exc:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # provider/network failures
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"文档索引失败：{exc}") from exc
    return document


@router.delete("/documents/{doc_id}")
def delete_document(
    doc_id: str,
    db: Database = Depends(get_db),
    config: AppConfig = Depends(get_config),
) -> dict:
    row = db.one("SELECT * FROM documents WHERE id = ?", (doc_id,))
    if not row:
        raise HTTPException(status_code=404, detail="文档不存在")
    db.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    stored = str(row["stored_path"] or "")
    if stored:
        path = Path(stored)
        if not path.is_absolute():
            path = config.base_dir / path
        try:
            path.resolve().relative_to(config.upload_dir.resolve())
            path.unlink(missing_ok=True)
        except (ValueError, OSError):
            pass
    return {"ok": True}


@router.get("/documents/{doc_id}/chunks")
def document_chunks(
    doc_id: str, limit: int = 6, db: Database = Depends(get_db)
) -> dict:
    row = db.one("SELECT * FROM documents WHERE id = ?", (doc_id,))
    if not row:
        raise HTTPException(status_code=404, detail="文档不存在")
    chunks = db.all(
        "SELECT chunk_index, content, page, section FROM chunks "
        "WHERE doc_id = ? ORDER BY chunk_index LIMIT ?",
        (doc_id, max(1, min(limit, 50))),
    )
    return {
        "document": document_dict(row),
        "items": [
            {
                "chunk_index": chunk["chunk_index"],
                "content": chunk["content"],
                "page": chunk["page"],
                "section": chunk["section"],
            }
            for chunk in chunks
        ],
    }


@router.post("/kbs/{kb_id}/reindex")
def reindex(
    kb_id: str,
    db: Database = Depends(get_db),
    store: SettingsStore = Depends(get_store),
) -> dict:
    _kb_or_404(db, kb_id)
    try:
        count = reindex_kb(db, store.get(), kb_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"重建索引失败：{exc}") from exc
    return {"ok": True, "chunks": count}


@router.post("/search")
def search(
    payload: SearchRequest,
    db: Database = Depends(get_db),
    store: SettingsStore = Depends(get_store),
) -> dict:
    _kb_or_404(db, payload.kb_id)
    hits, info = retrieve(
        db, store.get(), payload.kb_id, payload.query, top_k=payload.top_k
    )
    return {
        "query": payload.query,
        "info": info,
        "items": [
            hit.as_source(index, payload.query)
            for index, hit in enumerate(hits, start=1)
        ],
    }
