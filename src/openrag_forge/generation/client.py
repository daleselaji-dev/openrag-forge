from __future__ import annotations

import json
import logging
from typing import Any

from ..config import Settings
from ..domain.models import Evidence
from ..net import get_http_client
from ..observability import observe_fallback, start_span

logger = logging.getLogger(__name__)


def extractive_answer(question: str, evidence: list[Evidence]) -> str:
    if not evidence:
        return "当前知识库没有足够证据支持回答。请补充文档或改写问题。"
    lines = ["根据当前知识库检索到的证据："]
    for item in evidence[:3]:
        excerpt = item.text[:420].strip()
        lines.append(f"[{item.citation}] {excerpt}")
    lines.append("以上内容是文档证据摘要，不代表账户调查结果、退款承诺或法律结论。")
    return "\n".join(lines)


def generate_grounded_answer(
    question: str,
    evidence: list[Evidence],
    settings: Settings,
    *,
    profile: dict[str, Any] | None = None,
    temperature: float = 0.1,
    max_tokens: int = 600,
) -> tuple[str, str]:
    """Call an OpenAI-compatible chat endpoint, falling back safely when unavailable."""
    if not evidence:
        return "当前知识库没有足够证据支持回答。请补充文档或改写问题。", "no_evidence"
    context = "\n\n".join(f"[{item.citation}] {item.text}" for item in evidence)
    prompt = (
        "你是一个有证据边界的知识库助手。只根据下面的证据回答问题。"
        "每个事实性表述必须引用 [S#]；证据不足就明确说不知道。"
        "不要承诺退款，不要认定违法，不要推断账户状态。只输出中文简洁回答。\n\n"
        f"问题：{question}\n证据：\n{context}"
    )
    base_url = str(profile["base_url"]) if profile else settings.chat_base_url
    model_name = str(profile["model_name"]) if profile else settings.chat_model
    api_key = (profile.get("api_key") if profile else None) or settings.chat_api_key or settings.model_api_key
    if not base_url or model_name == "local-chat-model":
        return extractive_answer(question, evidence), "extractive_fallback"
    span_attributes: dict[str, Any] = {
        "gen_ai.system": "OpenAI-compatible",
        "gen_ai.operation.name": "chat",
        "gen_ai.request.model": model_name,
        "gen_ai.request.temperature": temperature,
        "gen_ai.request.max_tokens": max_tokens,
        "evidence_count": len(evidence),
    }
    if settings.langfuse_capture_content:
        span_attributes["gen_ai.prompt"] = json.dumps(
            [{"role": "system", "content": "You answer only from supplied evidence."}, {"role": "user", "content": prompt}],
            ensure_ascii=False,
        )
    with start_span("rag.llm.generate", span_attributes) as span:
        try:
            headers: dict[str, str] = {}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            response = get_http_client().post(
                f"{base_url.rstrip('/')}/chat/completions",
                json={
                    "model": model_name,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "messages": [
                        {"role": "system", "content": "You answer only from supplied evidence."},
                        {"role": "user", "content": prompt},
                    ],
                },
                headers=headers,
                timeout=settings.chat_timeout_seconds,
            )
            response.raise_for_status()
            content = str(response.json().get("choices", [{}])[0].get("message", {}).get("content", "")).strip()
            if not content:
                raise RuntimeError("模型返回空回答")
            payload = response.json()
            if span is not None:
                span.set_attribute("provider", "openai_compatible_chat")
                span.set_attribute("gen_ai.response.model", str(payload.get("model", model_name)))
                usage = payload.get("usage") or {}
                if usage.get("prompt_tokens") is not None:
                    span.set_attribute("gen_ai.usage.input_tokens", int(usage["prompt_tokens"]))
                if usage.get("completion_tokens") is not None:
                    span.set_attribute("gen_ai.usage.output_tokens", int(usage["completion_tokens"]))
                if settings.langfuse_capture_content:
                    span.set_attribute("gen_ai.completion", json.dumps([{"role": "assistant", "content": content}], ensure_ascii=False))
            return content, "openai_compatible_chat"
        except Exception as exc:
            logger.warning("LLM 生成降级为摘要式回答", extra={"error_type": type(exc).__name__, "chat_base_url": base_url})
            observe_fallback("llm_generate")
            if span is not None:
                span.set_attribute("provider", "extractive_fallback")
                span.set_attribute("fallback_reason", type(exc).__name__)
            return extractive_answer(question, evidence), "extractive_fallback"
