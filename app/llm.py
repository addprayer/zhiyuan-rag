"""OpenAI-compatible chat completion client with streaming support."""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx


class LLMError(RuntimeError):
    pass


def chat_endpoint(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise LLMError("LLM API 地址为空")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


class LLMClient:
    def __init__(self, settings: dict[str, Any]):
        self.base_url = str(settings.get("llm_base_url", ""))
        self.api_key = str(settings.get("llm_api_key", ""))
        self.model = str(settings.get("llm_model", ""))
        self.temperature = float(settings.get("llm_temperature", 0.2))
        self.max_tokens = int(settings.get("llm_max_tokens", 1200))
        if not self.api_key:
            raise LLMError("未配置 LLM API Key")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _payload(
        self, messages: list[dict[str, str]], stream: bool, max_tokens: int | None = None
    ) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": max_tokens or self.max_tokens,
            "stream": stream,
        }

    async def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[str]:
        url = chat_endpoint(self.base_url)
        timeout = httpx.Timeout(120, connect=20)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    "POST",
                    url,
                    headers=self._headers(),
                    json=self._payload(messages, stream=True),
                ) as response:
                    if response.status_code >= 400:
                        body = (await response.aread()).decode("utf-8", "replace")
                        raise LLMError(
                            f"LLM API {response.status_code}: {body[:400]}"
                        )
                    async for line in response.aiter_lines():
                        line = (line or "").strip()
                        if not line or not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            payload = json.loads(data)
                        except ValueError:
                            continue
                        choices = payload.get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta") or {}
                        text = delta.get("content")
                        if text:
                            yield str(text)
        except httpx.HTTPError as exc:
            raise LLMError(f"LLM 请求失败：{exc}") from exc

    async def complete(
        self, messages: list[dict[str, str]], max_tokens: int = 32
    ) -> str:
        url = chat_endpoint(self.base_url)
        timeout = httpx.Timeout(60, connect=15)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    url,
                    headers=self._headers(),
                    json=self._payload(messages, stream=False, max_tokens=max_tokens),
                )
        except httpx.HTTPError as exc:
            raise LLMError(f"LLM 请求失败：{exc}") from exc
        if response.status_code >= 400:
            raise LLMError(f"LLM API {response.status_code}: {response.text[:400]}")
        try:
            data = response.json()
            return str(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMError("LLM API 返回格式无法解析") from exc
