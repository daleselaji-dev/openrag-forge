# Architecture

```text
Upload → Route → Parse → Blocks → Chunk → Enrich → Embed/Index
                                             ↓
Question → Recipe Compiler → Retrieve → Context → Generate → Policy Gate
                                             ↓
                              Evidence Capsule + Trace + Eval
```

Lite uses SQLite and local artifacts. Production adapters implement the same repository ports using PostgreSQL and MinIO. Qdrant is always treated as a rebuildable derived index.

The web canvas is an authoring surface; the published Recipe JSON is the execution source of truth. Only registered nodes with typed ports may run.

