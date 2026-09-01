from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class Document(BaseModel):
    document_id: str
    knowledge_base_id: str
    filename: str
    media_type: str
    size_bytes: int
    sha256: str
    status: Literal["uploaded", "parsed", "failed", "quarantined"] = "uploaded"
    parser_route: str | None = None
    parser_confidence: float | None = None
    reason_codes: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)
    version: int = 1


class ParsedBlock(BaseModel):
    block_id: str
    document_id: str
    block_type: Literal["heading", "paragraph", "table", "row", "page", "code", "unknown"] = "paragraph"
    text: str
    order: int
    page: int | None = None
    heading_path: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Chunk(BaseModel):
    chunk_id: str
    document_id: str
    text: str
    order: int
    block_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Evidence(BaseModel):
    citation: str
    chunk_id: str
    document_id: str
    title: str
    text: str
    score: float = 0.0
    source_url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TraceEvent(BaseModel):
    run_id: str
    node_id: str
    sequence: int
    status: Literal["running", "completed", "failed", "skipped"]
    summary: str
    duration_ms: float = 0.0
    details: dict[str, Any] = Field(default_factory=dict)
    # 关联的 OpenTelemetry trace_id：把业务审计 Trace 与 Jaeger 里的
    # 性能链路串起来（在 Jaeger 搜索框粘贴此值即可）。tracing 关闭时为 None。
    otel_trace_id: str | None = None
    created_at: str = Field(default_factory=utc_now)


class RecipeNode(BaseModel):
    id: str
    type: str
    label: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class RecipeEdge(BaseModel):
    source: str
    source_port: str
    target: str
    target_port: str


class Recipe(BaseModel):
    recipe_id: str
    name: str
    version: str = "0.1.0"
    status: Literal["draft", "validated", "published", "deprecated"] = "draft"
    nodes: list[RecipeNode]
    edges: list[RecipeEdge]
    hash: str | None = None
    created_at: str = Field(default_factory=utc_now)


class RunResult(BaseModel):
    run_id: str
    recipe_id: str
    recipe_hash: str
    status: Literal["running", "completed", "failed"]
    answer: str | None = None
    artifact: dict[str, Any] | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    safety: dict[str, Any] = Field(default_factory=dict)
    trace: list[TraceEvent] = Field(default_factory=list)
