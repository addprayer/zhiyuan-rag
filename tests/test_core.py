"""Focused unit and API tests for 知源 (ZhiYuan)."""

from __future__ import annotations

import uuid
from pathlib import Path

import numpy as np

from app.chunking import Segment, chunk_segments
from app.db import Database, new_id, now_iso
from app.embeddings import LocalHashEmbedder
from app.prompts import demo_answer
from app.retrieval import RetrievalHit, retrieve
from app.text_utils import split_sentences, tokenize
from app.vector_store import pack_vector

WORK_ROOT = Path(__file__).resolve().parent / "_work"


def _workdir(label: str) -> Path:
    """Use a workspace-local scratch dir; CI temp dirs can be ACL-restricted on Windows."""
    path = WORK_ROOT / f"{label}-{uuid.uuid4().hex[:8]}"
    path.mkdir(parents=True, exist_ok=True)
    return path



def test_tokenize_keeps_chinese_bigrams():
    tokens = tokenize("年假有几天？可以转到明年吗？")
    assert "年假" in tokens
    assert "明年" in tokens
    assert "的" not in tokens


def test_split_sentences_handles_chinese_punctuation():
    parts = split_sentences("第一句。第二句！第三句？")
    assert parts == ["第一句。", "第二句！", "第三句？"]


def test_local_embedding_is_normalised_and_relevant():
    embedder = LocalHashEmbedder(dim=768)
    query = embedder.embed_query("年假可以结转吗")
    related = embedder.embed_query("年假最多结转五天，次年第一季度用完")
    unrelated = embedder.embed_query("服务器密码每九十天更换一次")
    assert query.shape == (768,)
    assert abs(float(np.linalg.norm(query)) - 1.0) < 1e-5
    assert float(np.dot(query, related)) > float(np.dot(query, unrelated))


def test_chunking_respects_size_and_overlap():
    text = "年假按司龄计算。入职满一年享受五天年假。满三年享受十天年假。满五年享受十五天年假。" * 12
    chunks = chunk_segments([Segment(text=text, section="年假制度")], chunk_size=300, overlap=60)
    assert len(chunks) > 1
    assert all(len(chunk.content) <= 300 for chunk in chunks)
    assert all(chunk.section == "年假制度" for chunk in chunks)
    assert [chunk.index for chunk in chunks] == list(range(len(chunks)))


def test_retrieval_returns_cited_hit():
    work = _workdir("retrieval")
    db = Database(work / "test.db")
    db.init()
    kb_id = new_id("kb")
    doc_id = new_id("doc")
    db.execute(
        "INSERT INTO kbs (id, name, description, embedding_signature, created_at) VALUES (?,?,?,?,?)",
        (kb_id, "测试库", "", "local-hash-768-v1", now_iso()),
    )
    db.execute(
        """
        INSERT INTO documents
            (id, kb_id, name, stored_path, file_type, size, page_count, chunk_count, status, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (doc_id, kb_id, "员工手册.md", "", ".md", 100, None, 1, "ready", now_iso()),
    )
    content = "问：年假可以转到明年吗？答：最多结转五天，且需在次年第一季度用完。"
    vector = LocalHashEmbedder().embed_documents([content])[0]
    db.execute(
        """
        INSERT INTO chunks
            (doc_id, kb_id, chunk_index, content, page, section, embedding, dim, created_at)
        VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (doc_id, kb_id, 0, content, None, "年假", pack_vector(vector), len(vector), now_iso()),
    )

    hits, info = retrieve(
        db,
        {"embedding_provider": "local", "hybrid_alpha": 0.65},
        kb_id,
        "年假可以结转吗",
        top_k=3,
    )
    assert info["vector_available"] is True
    assert hits
    assert hits[0].document_name == "员工手册.md"
    assert "结转" in hits[0].content


def test_demo_answer_strips_faq_question_and_keeps_citation():
    hit = RetrievalHit(
        chunk_id=1,
        document_id="doc_1",
        document_name="员工手册.md",
        page=None,
        section="年假",
        chunk_index=0,
        content="问：年假可以转到明年吗？答：最多结转五天，且需在次年第一季度用完。",
        vector_score=0.8,
        lexical_score=0.9,
        score=0.85,
    )
    answer = demo_answer("年假可以转到明年吗", [hit])
    assert "最多结转五天" in answer
    assert "[1]" in answer
    assert "问：年假可以转到明年吗" not in answer


def test_health_endpoint(monkeypatch):
    work = _workdir("api")
    monkeypatch.setenv("ZHIYUAN_DATA_DIR", str(work))
    monkeypatch.setenv("ZHIYUAN_SEED_DEMO", "1")

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        response = client.get("/api/health")
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "ok"
        assert payload["name"] == "知源"

        kbs = client.get("/api/kbs").json()["items"]
        assert kbs, "演示知识库应自动创建"
        assert kbs[0]["chunk_count"] > 0
