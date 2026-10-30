"""Prompt assembly and the no-API-key extractive fallback."""

from __future__ import annotations

import re
from typing import Any

from .retrieval import RetrievalHit
from .text_utils import split_sentences, tokenize

SYSTEM_PROMPT = """你是「知源」，一个企业知识库问答助手。

你的回答必须遵守以下规则：
1. 只依据「参考资料」中的事实回答，不使用资料之外的知识，也不要猜测。
2. 每个关键结论后面用 [编号] 标注来源，例如 [1]；编号必须对应参考资料编号。
3. 如果资料不足以回答，直接说明“知识库中没有找到足够的信息”，并建议用户补充文档或换个问法。
4. 如果不同资料存在冲突，指出冲突并分别标注来源。
5. 用简体中文回答，先给结论，再给必要的步骤或细节；不要复述整个参考资料。
6. 不要编造文件名、页码、数字、日期或流程。"""

NO_ANSWER = (
    "知识库中没有找到足够相关的内容。\n\n"
    "可以尝试换一种问法，或在左侧上传包含相关信息的文档后再问。"
)


def format_context(hits: list[RetrievalHit]) -> str:
    if not hits:
        return "（没有检索到相关资料）"
    blocks: list[str] = []
    for index, hit in enumerate(hits, start=1):
        location = f"《{hit.document_name}》"
        if hit.page:
            location += f" 第 {hit.page} 页"
        if hit.section:
            location += f" · {hit.section}"
        blocks.append(f"[{index}] 来源：{location}\n{hit.content}")
    return "\n\n".join(blocks)


def build_messages(
    question: str, hits: list[RetrievalHit], history: list[dict[str, Any]]
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for item in history[-6:]:
        role = item.get("role")
        content = str(item.get("content", "")).strip()
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content[:1200]})
    user_prompt = (
        "参考资料：\n"
        f"{format_context(hits)}\n\n"
        f"用户问题：{question}\n\n"
        "请依据参考资料回答，并在关键结论后标注来源编号。"
    )
    messages.append({"role": "user", "content": user_prompt})
    return messages


_BULLET_RE = re.compile(r"^[-*•·]\s*")


def _candidates(content: str) -> list[str]:
    """Prefer whole FAQ lines and list items over brittle sentence splits."""
    result: list[str] = []
    for raw_line in content.splitlines():
        line = _BULLET_RE.sub("", raw_line.strip())
        if not line or line.startswith("#") or line.startswith("|"):
            continue
        # FAQ lines such as "问：……？答：……。" should stay together, but the
        # repeated question adds no value in an extractive answer.
        if "答：" in line:
            answer = line.split("答：", 1)[1].strip()
            if len(answer) >= 8:
                result.append(answer)
        else:
            result.extend(part.strip() for part in split_sentences(line))
    return [item for item in result if len(item) >= 8]


def _ranked_candidates(
    hits: list[RetrievalHit], query: str, limit: int = 4
) -> list[tuple[int, str]]:
    """Rank candidate sentences globally so lower-ranked chunks cannot add noise."""
    query_tokens = set(tokenize(query))
    scored: list[tuple[float, int, str]] = []
    for index, hit in enumerate(hits[:3], start=1):
        for sentence in _candidates(hit.content):
            overlap = len(query_tokens & set(tokenize(sentence)))
            if overlap:
                scored.append((overlap / max(1, len(query_tokens)), index, sentence))
    if not scored:
        fallback = _BULLET_RE.sub("", hits[0].content.strip())[:180].strip() if hits else ""
        return [(1, fallback)] if fallback else []

    scored.sort(key=lambda item: item[0], reverse=True)
    threshold = max(0.26, scored[0][0] * 0.35)
    result: list[tuple[int, str]] = []
    seen: list[str] = []
    for score, index, sentence in scored:
        if score < threshold:
            break
        if any(sentence in existing or existing in sentence for existing in seen):
            continue
        result.append((index, sentence))
        seen.append(sentence)
        if len(result) >= limit:
            break
    if not result:
        result.append((scored[0][1], scored[0][2]))
    return result


def demo_answer(question: str, hits: list[RetrievalHit]) -> str:
    """A transparent extractive answer used when no LLM key is configured."""
    if not hits:
        return NO_ANSWER

    lines: list[str] = []
    if hits[0].score < 0.12:
        lines.append("检索到的内容相关度较低，以下信息仅供参考：\n")
    else:
        lines.append("根据知识库中的资料：\n")

    candidates = _ranked_candidates(hits, question)
    if not candidates:
        return NO_ANSWER
    for index, sentence in candidates:
        lines.append(f"- {sentence} [{index}]")
    lines.append(
        "\n（演示模式：未配置大模型 API Key，以上内容由检索片段自动摘录，"
        "未经生成式改写。配置模型后可获得更自然的总结回答。）"
    )
    return "\n".join(lines)
