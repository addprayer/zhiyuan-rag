"""Model, embedding and retrieval settings."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..db import Database
from ..embeddings import EmbeddingError, build_embedder, embedding_label, signature_for
from ..llm import LLMClient, LLMError
from ..schemas import SettingsUpdate, TestEmbeddingRequest, TestLLMRequest
from .deps import get_db, get_store

router = APIRouter(prefix="/api/settings", tags=["settings"])

_MASK_PREFIXES = ("••••", "已保存")


def _reindex_required(db: Database, signature: str) -> int:
    row = db.one(
        "SELECT COUNT(*) AS n FROM kbs WHERE embedding_signature != ?", (signature,)
    )
    return int(row["n"]) if row else 0


def _clean_secret(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if value.startswith(_MASK_PREFIXES):
        return None
    return value


@router.get("")
def read_settings(db: Database = Depends(get_db), store=Depends(get_store)) -> dict:
    settings = store.get()
    return {
        "settings": store.public(),
        "embedding_label": embedding_label(settings),
        "reindex_required": _reindex_required(db, signature_for(settings)),
    }


@router.put("")
def update_settings(
    payload: SettingsUpdate,
    db: Database = Depends(get_db),
    store=Depends(get_store),
) -> dict:
    patch = payload.model_dump(exclude_unset=True)
    clear_llm = bool(patch.pop("clear_llm_key", False))
    clear_embedding = bool(patch.pop("clear_embedding_key", False))

    for key in ("llm_api_key", "embedding_api_key"):
        if key in patch:
            cleaned = _clean_secret(patch.get(key))
            if cleaned is None:
                patch.pop(key, None)
            else:
                patch[key] = cleaned
    if clear_llm:
        patch["llm_api_key"] = ""
    if clear_embedding:
        patch["embedding_api_key"] = ""

    provider = patch.get("embedding_provider")
    if provider is not None and provider not in {"local", "openai"}:
        raise HTTPException(status_code=400, detail="Embedding 模式只能是 local 或 openai")

    merged = store.get()
    merged.update({key: value for key, value in patch.items() if value is not None})
    if int(merged["chunk_overlap"]) >= int(merged["chunk_size"]):
        raise HTTPException(status_code=400, detail="片段重叠必须小于片段长度")

    updated = store.update(patch)
    return {
        "settings": store.public(),
        "embedding_label": embedding_label(updated),
        "reindex_required": _reindex_required(db, signature_for(updated)),
    }


@router.post("/test-llm")
async def test_llm(payload: TestLLMRequest, store=Depends(get_store)) -> dict:
    merged = store.get()
    overrides = payload.model_dump(exclude_unset=True)
    if overrides.get("llm_api_key"):
        cleaned = _clean_secret(overrides["llm_api_key"])
        if cleaned:
            merged["llm_api_key"] = cleaned
    for key in ("llm_base_url", "llm_model"):
        if overrides.get(key):
            merged[key] = overrides[key]
    try:
        client = LLMClient(merged)
        text = await client.complete(
            [{"role": "user", "content": "只回复四个字：连接成功"}], max_tokens=24
        )
        return {"ok": True, "message": text.strip()[:120], "model": merged["llm_model"]}
    except LLMError as exc:
        return {"ok": False, "message": str(exc)}


@router.post("/test-embedding")
def test_embedding(payload: TestEmbeddingRequest, store=Depends(get_store)) -> dict:
    merged = store.get()
    overrides = payload.model_dump(exclude_unset=True)
    if overrides.get("embedding_api_key"):
        cleaned = _clean_secret(overrides["embedding_api_key"])
        if cleaned:
            merged["embedding_api_key"] = cleaned
    for key in ("embedding_provider", "embedding_base_url", "embedding_model"):
        if overrides.get(key):
            merged[key] = overrides[key]
    try:
        embedder = build_embedder(merged)
        vector = embedder.embed_query("企业知识库检索连接测试")
        return {
            "ok": True,
            "dim": int(len(vector)),
            "message": f"向量维度 {len(vector)}",
            "label": embedding_label(merged),
        }
    except EmbeddingError as exc:
        return {"ok": False, "message": str(exc)}
