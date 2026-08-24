from __future__ import annotations

import httpx

from ..config import Settings
from ..domain.models import Evidence


def generate_grounded_answer(question: str, evidence: list[Evidence], settings: Settings) -> tuple[str, str]:
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
    if not settings.chat_base_url or settings.chat_model == "local-chat-model":
        from ..app import _extractive_answer
        return _extractive_answer(question, evidence), "extractive_fallback"
    try:
        response = httpx.post(f"{settings.chat_base_url.rstrip('/')}/chat/completions", json={"model": settings.chat_model, "temperature": 0.1, "max_tokens": 600, "messages": [{"role": "system", "content": "You answer only from supplied evidence."}, {"role": "user", "content": prompt}]}, timeout=90)
        response.raise_for_status()
        content = str(response.json().get("choices", [{}])[0].get("message", {}).get("content", "")).strip()
        if not content:
            raise RuntimeError("模型返回空回答")
        return content, "openai_compatible_chat"
    except Exception:
        from ..app import _extractive_answer
        return _extractive_answer(question, evidence), "extractive_fallback"

