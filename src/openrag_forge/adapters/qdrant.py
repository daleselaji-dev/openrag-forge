from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5

from ..config import Settings
from ..domain.models import Chunk
from ..net import get_http_client
from ..observability import start_span


class QdrantAdapter:
    """Optional derived index adapter. It never becomes the truth source."""

    def __init__(self, config: Settings):
        self.config = config
        self.base = config.qdrant_url.rstrip("/")

    def _embedding_url(self) -> str:
        return f"{self.config.embedding_base_url.rstrip('/')}/embeddings"

    def _auth_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        token = self.config.embedding_api_key or self.config.model_api_key
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def embed(self, texts: list[str]) -> list[list[float]]:
        with start_span("rag.embed", {"input_count": len(texts), "model": self.config.embedding_model}):
            response = get_http_client().post(
                self._embedding_url(),
                json={"model": self.config.embedding_model, "input": texts},
                headers=self._auth_headers(),
                timeout=self.config.embedding_timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            return [item["embedding"] for item in sorted(payload.get("data", []), key=lambda item: item.get("index", 0))]

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.config.qdrant_api_key:
            headers["api-key"] = self.config.qdrant_api_key
        return headers

    def ensure_collection(self, dimension: int) -> None:
        collection_url = f"{self.base}/collections/{self.config.qdrant_collection}"
        client = get_http_client()
        response = client.get(collection_url, headers=self._headers(), timeout=self.config.http_timeout_seconds)
        if response.status_code == 200:
            return
        create = client.put(
            collection_url,
            headers=self._headers(),
            json={"vectors": {"size": dimension, "distance": "Cosine"}},
            timeout=self.config.http_timeout_seconds,
        )
        create.raise_for_status()

    def index(self, chunks: list[Chunk]) -> dict[str, int | str]:
        if not chunks:
            return {"status": "empty", "indexed": 0}
        with start_span("rag.qdrant.index", {"chunk_count": len(chunks), "collection": self.config.qdrant_collection}):
            vectors = self.embed([chunk.text for chunk in chunks])
            if len(vectors) != len(chunks):
                raise RuntimeError("Embedding 返回数量与 Chunk 不一致")
            self.ensure_collection(len(vectors[0]))
            points = []
            for chunk, vector in zip(chunks, vectors, strict=True):
                points.append(
                    {
                        "id": str(uuid5(NAMESPACE_URL, f"openrag:{chunk.chunk_id}")),
                        "vector": vector,
                        "payload": {"chunk_id": chunk.chunk_id, "document_id": chunk.document_id, "text": chunk.text, **chunk.metadata},
                    }
                )
            response = get_http_client().put(
                f"{self.base}/collections/{self.config.qdrant_collection}/points?wait=true",
                headers=self._headers(),
                json={"points": points},
                timeout=self.config.index_timeout_seconds,
            )
            response.raise_for_status()
            return {"status": "indexed", "indexed": len(points), "collection": self.config.qdrant_collection}

    def search(self, question: str, limit: int) -> list[dict]:
        with start_span("rag.qdrant.search", {"limit": limit, "collection": self.config.qdrant_collection}) as span:
            vector = self.embed([question])[0]
            response = get_http_client().post(
                f"{self.base}/collections/{self.config.qdrant_collection}/points/search",
                headers=self._headers(),
                json={"vector": vector, "limit": limit, "with_payload": True},
                timeout=self.config.http_timeout_seconds,
            )
            response.raise_for_status()
            results = response.json().get("result", [])
            if span is not None:
                span.set_attribute("result_count", len(results))
            return results
