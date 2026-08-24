"""Metadata 增强：为 Chunk 补充 title / language / keywords。

真实实现（非直通）：增强字段随 Chunk 持久化进 SQLite 真相源，
并进入 Qdrant payload 与 Evidence metadata，供 metadata_filter 与引用展示消费。
"""

from __future__ import annotations

import re
from collections import Counter

from ..domain.models import Chunk, ParsedBlock
from ..retrieval.lexical import tokenize


def _detect_language(text: str) -> str:
    han = len(re.findall(r"[\u4e00-\u9fff]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    if han == 0 and latin == 0:
        return "unknown"
    if han >= latin:
        return "zh" if latin < han * 0.3 else "mixed"
    return "en" if han < latin * 0.05 else "mixed"


def enrich_chunks(chunks: list[Chunk], blocks: list[ParsedBlock], filename: str, keywords_top_k: int = 5) -> list[Chunk]:
    block_by_id = {block.block_id: block for block in blocks}
    headings: dict[int, str] = {block.order: block.text for block in blocks if block.block_type == "heading"}
    enriched: list[Chunk] = []
    for chunk in chunks:
        metadata = dict(chunk.metadata)
        source_block = block_by_id.get(chunk.block_ids[0]) if chunk.block_ids else None
        title = None
        if source_block is not None:
            if source_block.heading_path:
                title = source_block.heading_path[-1]
            else:
                # 最近的前置 heading Block 作为标题
                candidates = [text for order, text in headings.items() if order <= source_block.order]
                title = candidates[-1] if candidates else None
        metadata["title"] = (title or filename)[:120]
        metadata["language"] = _detect_language(chunk.text)
        if keywords_top_k > 0:
            counts = Counter(token for token in tokenize(chunk.text) if len(token) > 1)
            metadata["keywords"] = [token for token, _ in counts.most_common(keywords_top_k)]
        enriched.append(chunk.model_copy(update={"metadata": metadata}))
    return enriched
