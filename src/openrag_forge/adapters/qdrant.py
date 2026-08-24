from __future__ import annotations

import re
from uuid import NAMESPACE_URL, uuid5

import httpx

from ..config import Settings
from ..domain.models import Chunk


class QdrantAdapter:
    """Optional derived index adapter. It never becomes the truth source."""

    def __init__(self, config: Settings):
        self.config = config
        self.base = config.qdrant_url.rstrip("/")

    def _embedding_url(self) -> str:
        return f"{self.config.embedding_base_url.rstrip('/')}/embeddings"

    def embed(self, texts: list[str]) -> list[list[float]]:
        response = httpx.post(self._embedding_url(), json={"model": self.config.embedding_model, "input": texts}, timeout=60)
        response.raise_for_status()
        payload = response.json()
        return [item["embedding"] for item in sorted(payload.get("data", []), key=lambda item: item.get("index", 0))]

    def _headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json"}

    def ensure_collection(self, dimension: int) -> None:
        collection_url = f"{self.base}/collections/{self.config.qdrant_collection}"
        response = httpx.get(collection_url, timeout=10)
        if response.status_code == 200:
            return
        create = httpx.put(collection_url, headers=self._headers(), json={"vectors": {"size": dimension, "distance": "Cosine"}}, timeout=30)
        create.raise_for_status()

    def index(self, chunks: list[Chunk]) -> dict[str, int | str]:
        if not chunks:
            return {"status": "empty", "indexed": 0}
        vectors = self.embed([chunk.text for chunk in chunks])
        if len(vectors) != len(chunks):
            raise RuntimeError("Embedding 返回数量与 Chunk 不一致")
        self.ensure_collection(len(vectors[0]))
        points = []
        for chunk, vector in zip(chunks, vectors):
            points.append({"id": str(uuid5(NAMESPACE_URL, f"openrag:{chunk.chunk_id}")), "vector": vector, "payload": {"chunk_id": chunk.chunk_id, "document_id": chunk.document_id, "text": chunk.text, **chunk.metadata}})
        response = httpx.put(f"{self.base}/collections/{self.config.qdrant_collection}/points?wait=true", headers=self._headers(), json={"points": points}, timeout=120)
        response.raise_for_status()
        return {"status": "indexed", "indexed": len(points), "collection": self.config.qdrant_collection}

    def search(self, question: str, limit: int) -> list[dict]:
        vector = self.embed([question])[0]
        response = httpx.post(f"{self.base}/collections/{self.config.qdrant_collection}/points/search", headers=self._headers(), json={"vector": vector, "limit": limit, "with_payload": True}, timeout=60)
        response.raise_for_status()
        return response.json().get("result", [])

