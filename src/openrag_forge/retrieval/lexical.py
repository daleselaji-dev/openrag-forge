"""本地词法检索：纯 Python BM25，直接在 SQLite 真相源 Chunk 上打分。

这是 Lite 安装内置的 sparse 召回后端（backend=bm25_local），也是 dense 检索在
Qdrant / Embedding 服务离线时的降级路径（backend=lexical_fallback）。
"""

from __future__ import annotations

import math
import re

from ..domain.models import Chunk

_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "what", "which", "how", "can", "could",
    "should", "i", "you", "we", "they", "for", "to", "of", "in", "on", "and", "or",
    "does", "do", "did", "this", "that", "it", "with", "at", "by", "be",
}


def tokenize(text: str) -> list[str]:
    """混合分词：英文按词、中文按字，过滤停用词。确定性、零依赖。"""
    lowered = text.lower()
    tokens = re.findall(r"[a-z0-9][a-z0-9_\-.]*|[\u4e00-\u9fff]", lowered)
    return [token for token in tokens if token not in _STOPWORDS]


def token_set(text: str) -> set[str]:
    return set(tokenize(text))


def bm25_rank(query: str, chunks: list[Chunk], top_k: int = 20, k1: float = 1.5, b: float = 0.75) -> list[tuple[float, Chunk]]:
    """在真相源 Chunk 上执行 BM25 排序，返回 (score, chunk) 降序列表。"""
    query_tokens = tokenize(query)
    if not query_tokens or not chunks:
        return []
    docs = [tokenize(chunk.text) for chunk in chunks]
    doc_count = len(docs)
    avg_len = sum(len(doc) for doc in docs) / doc_count
    frequencies: dict[str, int] = {}
    for doc in docs:
        for token in set(doc):
            frequencies[token] = frequencies.get(token, 0) + 1
    scored: list[tuple[float, Chunk]] = []
    unique_query = set(query_tokens)
    for doc, chunk in zip(docs, chunks, strict=True):
        score = 0.0
        length = len(doc) or 1
        counts: dict[str, int] = {}
        for token in doc:
            if token in unique_query:
                counts[token] = counts.get(token, 0) + 1
        for token in unique_query:
            tf = counts.get(token, 0)
            if tf == 0:
                continue
            df = frequencies.get(token, 0)
            idf = math.log(1 + (doc_count - df + 0.5) / (df + 0.5))
            score += idf * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * length / avg_len))
        if score > 0:
            scored.append((round(score, 4), chunk))
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[:top_k]


def overlap_rank(query: str, chunks: list[Chunk], top_k: int = 20) -> list[tuple[float, Chunk]]:
    """轻量词重叠打分（dense 降级路径），归一化到 0-1。"""
    query_tokens = token_set(query)
    if not query_tokens:
        return []
    scored: list[tuple[float, Chunk]] = []
    for chunk in chunks:
        overlap = len(query_tokens & token_set(chunk.text))
        if overlap:
            scored.append((round(overlap / max(1, len(query_tokens)), 4), chunk))
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[:top_k]
