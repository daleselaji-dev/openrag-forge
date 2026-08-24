# Migrating from OpenSupport RAG

The previous `opensupport-rag` repository remains a runnable CFPB case study. OpenRAG Forge extracts its reusable concerns into provider-neutral contracts:

| OpenSupport concern | OpenRAG Forge contract |
|---|---|
| CFPB ComplaintRecord | generic Document + metadata adapter |
| Qdrant dense/sparse retrieval | retriever plugin and Recipe node |
| citation validation | policy plugin |
| stage preview | Recipe Preview + Trace |
| Golden Set | portable Eval JSONL |
| PostgreSQL/MinIO/Redis | production storage/queue adapters |

The CFPB downloaders, authority rules and support benchmark belong under `packs/customer-support-cfpb`; they are not imported by the framework core.

