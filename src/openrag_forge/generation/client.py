from __future__ import annotations

from typing import Any

import httpx

from ..config import Settings
from ..domain.models import Evidence


def extractive_answer(question: str, evidence: list[Evidence]) -> str:
    """确定性的抽取式回答：模型不可用或引用缺失时的降级路径。"""
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
    profile: dict[str, Any] | None = None,
    temperature: float = 0.1,
    max_tokens: int = 600,
) -> tuple[str, str]:
    """Call an OpenAI-compatible chat endpoint, falling back safely when unavailable.

    profile：模型注册表中的 chat 模型档案（base_url / model_name / api_key / parameters），
    覆盖全局 settings；api_key 只在服务端使用，永不进入 Trace。
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
    base_url = str(profile["base_url"]) if profile else settings.chat_base_url
    model_name = str(profile["model_name"]) if profile else settings.chat_model
    api_key = (profile.get("api_key") if profile else None) or settings.chat_api_key
    if not base_url or model_name == "local-chat-model":
        return extractive_answer(question, evidence), "extractive_fallback"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        response = httpx.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers=headers,
            json={
                "model": model_name,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "messages": [
                    {"role": "system", "content": "You answer only from supplied evidence."},
                    {"role": "user", "content": prompt},
                ],
            },
            timeout=90,
        )
        response.raise_for_status()
        content = str(response.json().get("choices", [{}])[0].get("message", {}).get("content", "")).strip()
        if not content:
            raise RuntimeError("模型返回空回答")
        return content, "openai_compatible_chat"
    except Exception:
        return extractive_answer(question, evidence), "extractive_fallback"
