from __future__ import annotations

import logging

from ..config import Settings
from ..domain.models import Evidence
from ..net import get_http_client
from ..observability import observe_fallback, start_span

logger = logging.getLogger(__name__)


def generate_grounded_answer(question: str, evidence: list[Evidence], settings: Settings) -> tuple[str, str]:
    """Call an OpenAI-compatible chat endpoint, falling back safely when unavailable.

    生产化改造点：
    1. 复用共享连接池 + settings.chat_timeout_seconds 显式超时；
    2. 支持云端模型的 Bearer 鉴权（OPENRAG_MODEL_API_KEY）；
    3. 整个生成过程包裹 OTel span（rag.llm.generate），带 provider/model 属性——
       LLM 是延迟大头，必须能在 Jaeger 里单独看到它的耗时；
    4. 降级不再静默：记录 warning 日志 + openrag_degraded_fallbacks_total 指标，
       保证"服务没挂但答案质量下降"的状态对运维可见、可告警。
    """
    if not evidence:
        return "当前知识库没有足够证据支持回答。请补充文档或改写问题。", "no_evidence"
    context = "\n\n".join(f"[{item.citation}] {item.text}" for item in evidence)
    prompt = (
        "你是一个有证据边界的知识库助手。只根据下面的证据回答问题。"
        "每个事实性表述必须引用 [S#]；证据不足就明确说不知道。"
        "不要承诺退款，不要认定违法，不要推断账户状态。只输出中文简洁回答。\n\n"
        f"问题：{question}\n证据：\n{context}"
    )
    if not settings.chat_base_url or settings.chat_model == "local-chat-model":
        from ..app import _extractive_answer

        return _extractive_answer(question, evidence), "extractive_fallback"
    with start_span("rag.llm.generate", {"model": settings.chat_model, "evidence_count": len(evidence)}) as span:
        try:
            headers = {}
            if settings.model_api_key:
                headers["Authorization"] = f"Bearer {settings.model_api_key}"
            response = get_http_client().post(
                f"{settings.chat_base_url.rstrip('/')}/chat/completions",
                json={
                    "model": settings.chat_model,
                    "temperature": 0.1,
                    "max_tokens": 600,
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
            if span is not None:
                span.set_attribute("provider", "openai_compatible_chat")
            return content, "openai_compatible_chat"
        except Exception as exc:
            # 明确记录降级原因（异常类型足够定位，不打全文避免日志注入与刷屏）
            logger.warning("LLM 生成降级为摘要式回答", extra={"error_type": type(exc).__name__, "chat_base_url": settings.chat_base_url})
            observe_fallback("llm_generate")
            if span is not None:
                span.set_attribute("provider", "extractive_fallback")
                span.set_attribute("fallback_reason", type(exc).__name__)
            from ..app import _extractive_answer

            return _extractive_answer(question, evidence), "extractive_fallback"
