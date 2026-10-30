"""Chat streaming with retrieval, citations and conversation history."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..db import Database, new_id, now_iso
from ..llm import LLMClient, LLMError
from ..prompts import NO_ANSWER, build_messages, demo_answer
from ..retrieval import retrieve
from ..schemas import ChatRequest
from .deps import get_db, get_store

router = APIRouter(prefix="/api", tags=["chat"])


def sse(event: str, data: dict) -> str:
    return (
        f"event: {event}\n"
        f"data: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"
    )


def _chunks(text: str, size: int = 28) -> list[str]:
    return [text[index : index + size] for index in range(0, len(text), size)]


def _conversation_or_404(db: Database, conversation_id: str):
    row = db.one("SELECT * FROM conversations WHERE id = ?", (conversation_id,))
    if not row:
        raise HTTPException(status_code=404, detail="会话不存在")
    return row


def _message_payload(row) -> dict:
    sources = []
    if row["sources"]:
        try:
            parsed = json.loads(row["sources"])
            if isinstance(parsed, list):
                sources = parsed
        except ValueError:
            sources = []
    return {
        "id": row["id"],
        "role": row["role"],
        "content": row["content"],
        "sources": sources,
        "mode": row["mode"],
        "created_at": row["created_at"],
    }


@router.post("/chat")
async def chat(
    payload: ChatRequest,
    request: Request,
    db: Database = Depends(get_db),
    store=Depends(get_store),
) -> StreamingResponse:
    kb = db.one("SELECT * FROM kbs WHERE id = ?", (payload.kb_id,))
    if not kb:
        raise HTTPException(status_code=404, detail="知识库不存在")

    settings = store.get()
    top_k = payload.top_k or int(settings.get("top_k", 5))
    hits, info = retrieve(db, settings, payload.kb_id, payload.question, top_k=top_k)
    sources = [
        hit.as_source(index, payload.question)
        for index, hit in enumerate(hits, start=1)
    ]

    conversation_id = payload.conversation_id
    if conversation_id:
        conversation = db.one(
            "SELECT * FROM conversations WHERE id = ? AND kb_id = ?",
            (conversation_id, payload.kb_id),
        )
        if not conversation:
            conversation_id = None
    if not conversation_id:
        conversation_id = new_id("conv")
        title = payload.question.strip().replace("\n", " ")[:26]
        timestamp = now_iso()
        db.execute(
            "INSERT INTO conversations (id, kb_id, title, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (conversation_id, payload.kb_id, title, timestamp, timestamp),
        )

    history_rows = db.all(
        "SELECT role, content FROM messages WHERE conversation_id = ? "
        "ORDER BY created_at DESC, rowid DESC LIMIT 8",
        (conversation_id,),
    )
    history = [dict(row) for row in reversed(history_rows)]

    user_message_id = new_id("msg")
    db.execute(
        "INSERT INTO messages (id, conversation_id, role, content, sources, mode, created_at) "
        "VALUES (?, ?, 'user', ?, NULL, NULL, ?)",
        (user_message_id, conversation_id, payload.question, now_iso()),
    )
    db.execute(
        "UPDATE conversations SET updated_at = ? WHERE id = ?",
        (now_iso(), conversation_id),
    )

    mode = "llm" if settings.get("llm_api_key") else "demo"
    assistant_message_id = new_id("msg")

    async def generate():
        full = ""
        error_message: str | None = None
        yield sse(
            "meta",
            {
                "conversation_id": conversation_id,
                "user_message_id": user_message_id,
                "assistant_message_id": assistant_message_id,
                "mode": mode,
                "retrieval": info,
            },
        )
        yield sse("sources", {"sources": sources})

        try:
            if not hits:
                text = NO_ANSWER
                for piece in _chunks(text):
                    full += piece
                    yield sse("delta", {"text": piece})
                    await asyncio.sleep(0.012)
            elif mode == "demo":
                text = demo_answer(payload.question, hits)
                for piece in _chunks(text, 22):
                    full += piece
                    yield sse("delta", {"text": piece})
                    await asyncio.sleep(0.014)
            else:
                client = LLMClient(settings)
                messages = build_messages(payload.question, hits, history)
                async for delta in client.stream(messages):
                    full += delta
                    yield sse("delta", {"text": delta})
                    if await request.is_disconnected():
                        break
        except LLMError as exc:
            error_message = str(exc)
            if not full:
                full = f"回答生成失败：{error_message}"
                yield sse("delta", {"text": full})
            yield sse("error", {"message": error_message})
        except Exception as exc:  # keep the SSE stream well-formed
            error_message = f"服务异常：{exc}"
            if not full:
                full = error_message
                yield sse("delta", {"text": full})
            yield sse("error", {"message": error_message})
        finally:
            db.execute(
                "INSERT INTO messages "
                "(id, conversation_id, role, content, sources, mode, created_at) "
                "VALUES (?, ?, 'assistant', ?, ?, ?, ?)",
                (
                    assistant_message_id,
                    conversation_id,
                    full,
                    json.dumps(sources, ensure_ascii=False) if sources else None,
                    mode,
                    now_iso(),
                ),
            )
            db.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now_iso(), conversation_id),
            )
            yield sse(
                "done",
                {
                    "conversation_id": conversation_id,
                    "assistant_message_id": assistant_message_id,
                    "mode": mode,
                    "error": error_message,
                },
            )

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/conversations")
def list_conversations(kb_id: str | None = None, db: Database = Depends(get_db)) -> dict:
    if kb_id:
        rows = db.all(
            "SELECT * FROM conversations WHERE kb_id = ? ORDER BY updated_at DESC LIMIT 40",
            (kb_id,),
        )
    else:
        rows = db.all(
            "SELECT * FROM conversations ORDER BY updated_at DESC LIMIT 40"
        )
    items = []
    for row in rows:
        count = db.one(
            "SELECT COUNT(*) AS n FROM messages WHERE conversation_id = ?",
            (row["id"],),
        )
        last = db.one(
            "SELECT content FROM messages WHERE conversation_id = ? "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (row["id"],),
        )
        items.append(
            {
                "id": row["id"],
                "kb_id": row["kb_id"],
                "title": row["title"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "message_count": int(count["n"]) if count else 0,
                "preview": (last["content"][:80] if last else ""),
            }
        )
    return {"items": items}


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: str, db: Database = Depends(get_db)) -> dict:
    conversation = _conversation_or_404(db, conversation_id)
    rows = db.all(
        "SELECT * FROM messages WHERE conversation_id = ? "
        "ORDER BY created_at ASC, rowid ASC",
        (conversation_id,),
    )
    return {
        "id": conversation["id"],
        "kb_id": conversation["kb_id"],
        "title": conversation["title"],
        "created_at": conversation["created_at"],
        "updated_at": conversation["updated_at"],
        "messages": [_message_payload(row) for row in rows],
    }


@router.delete("/conversations/{conversation_id}")
def delete_conversation(
    conversation_id: str, db: Database = Depends(get_db)
) -> dict:
    _conversation_or_404(db, conversation_id)
    db.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
    return {"ok": True}
