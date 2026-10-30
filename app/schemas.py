"""Request models."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class KBCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)
    description: str = Field("", max_length=240)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("知识库名称不能为空")
        return value

    @field_validator("description")
    @classmethod
    def clean_desc(cls, value: str) -> str:
        return value.strip()


class KBUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=60)
    description: str | None = Field(None, max_length=240)


class ChatRequest(BaseModel):
    kb_id: str = Field(..., min_length=1)
    question: str = Field(..., min_length=1, max_length=2000)
    conversation_id: str | None = None
    top_k: int | None = Field(None, ge=1, le=12)

    @field_validator("question")
    @classmethod
    def clean_question(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("问题不能为空")
        return value


class SearchRequest(BaseModel):
    kb_id: str = Field(..., min_length=1)
    query: str = Field(..., min_length=1, max_length=2000)
    top_k: int = Field(5, ge=1, le=20)

    @field_validator("query")
    @classmethod
    def clean_query(cls, value: str) -> str:
        return value.strip()


class SettingsUpdate(BaseModel):
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    llm_temperature: float | None = Field(None, ge=0, le=2)
    llm_max_tokens: int | None = Field(None, ge=64, le=8192)
    embedding_provider: str | None = None
    embedding_base_url: str | None = None
    embedding_api_key: str | None = None
    embedding_model: str | None = None
    top_k: int | None = Field(None, ge=1, le=12)
    chunk_size: int | None = Field(None, ge=200, le=2000)
    chunk_overlap: int | None = Field(None, ge=0, le=500)
    hybrid_alpha: float | None = Field(None, ge=0, le=1)
    clear_llm_key: bool = False
    clear_embedding_key: bool = False


class TestLLMRequest(BaseModel):
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None


class TestEmbeddingRequest(BaseModel):
    embedding_provider: str | None = None
    embedding_base_url: str | None = None
    embedding_api_key: str | None = None
    embedding_model: str | None = None
