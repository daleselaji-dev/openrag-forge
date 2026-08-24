from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .config import Settings
from .domain.models import Chunk, Document, ParsedBlock, Recipe, TraceEvent, utc_now


class Store:
    """Small SQLite truth source used by Lite; production adapters can implement this port."""

    def __init__(self, config: Settings):
        self.config = config
        config.data_dir.mkdir(parents=True, exist_ok=True)
        config.upload_dir.mkdir(parents=True, exist_ok=True)
        config.artifact_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.config.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS documents (
                  document_id TEXT PRIMARY KEY, knowledge_base_id TEXT NOT NULL,
                  payload TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS knowledge_bases (
                  knowledge_base_id TEXT PRIMARY KEY, name TEXT NOT NULL,
                  created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS blocks (
                  block_id TEXT PRIMARY KEY, document_id TEXT NOT NULL,
                  payload TEXT NOT NULL, block_order INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chunks (
                  chunk_id TEXT PRIMARY KEY, document_id TEXT NOT NULL,
                  payload TEXT NOT NULL, chunk_order INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS recipes (
                  recipe_id TEXT PRIMARY KEY, payload TEXT NOT NULL,
                  created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS model_registry (
                  model_id TEXT PRIMARY KEY, payload TEXT NOT NULL,
                  created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS scenarios (
                  scenario_id TEXT PRIMARY KEY, payload TEXT NOT NULL,
                  created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runs (
                  run_id TEXT PRIMARY KEY, payload TEXT NOT NULL,
                  created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS trace_events (
                  run_id TEXT NOT NULL, sequence INTEGER NOT NULL,
                  payload TEXT NOT NULL, PRIMARY KEY (run_id, sequence)
                );
                """
            )

    def save_document(self, document: Document, content: bytes) -> Path:
        destination = self.config.upload_dir / f"{document.document_id}_{document.filename}"
        destination.write_bytes(content)
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO documents VALUES (?, ?, ?, ?)", (document.document_id, document.knowledge_base_id, document.model_dump_json(), utc_now()))
        return destination

    def save_knowledge_base(self, knowledge_base_id: str, name: str) -> None:
        with self._connect() as db:
            db.execute("INSERT OR IGNORE INTO knowledge_bases VALUES (?, ?, ?)", (knowledge_base_id, name, utc_now()))

    def list_knowledge_bases(self) -> list[dict[str, str]]:
        with self._connect() as db:
            rows = db.execute("SELECT knowledge_base_id, name, created_at FROM knowledge_bases ORDER BY created_at").fetchall()
        return [dict(row) for row in rows]

    def update_document(self, document: Document) -> None:
        with self._connect() as db:
            db.execute("UPDATE documents SET payload=? WHERE document_id=?", (document.model_dump_json(), document.document_id))

    def save_blocks(self, blocks: list[ParsedBlock]) -> None:
        with self._connect() as db:
            db.executemany("INSERT OR REPLACE INTO blocks VALUES (?, ?, ?, ?)", [(b.block_id, b.document_id, b.model_dump_json(), b.order) for b in blocks])

    def save_chunks(self, chunks: list[Chunk]) -> None:
        with self._connect() as db:
            db.executemany("INSERT OR REPLACE INTO chunks VALUES (?, ?, ?, ?)", [(c.chunk_id, c.document_id, c.model_dump_json(), c.order) for c in chunks])

    def get_document(self, document_id: str) -> Document | None:
        with self._connect() as db:
            row = db.execute("SELECT payload FROM documents WHERE document_id=?", (document_id,)).fetchone()
        return Document.model_validate_json(row["payload"]) if row else None

    def list_documents(self, knowledge_base_id: str) -> list[Document]:
        with self._connect() as db:
            rows = db.execute("SELECT payload FROM documents WHERE knowledge_base_id=? ORDER BY created_at DESC", (knowledge_base_id,)).fetchall()
        return [Document.model_validate_json(row["payload"]) for row in rows]

    def list_blocks(self, document_id: str) -> list[ParsedBlock]:
        with self._connect() as db:
            rows = db.execute("SELECT payload FROM blocks WHERE document_id=? ORDER BY block_order", (document_id,)).fetchall()
        return [ParsedBlock.model_validate_json(row["payload"]) for row in rows]

    def list_chunks(self, knowledge_base_id: str) -> list[Chunk]:
        with self._connect() as db:
            rows = db.execute("SELECT c.payload FROM chunks c JOIN documents d ON d.document_id=c.document_id WHERE d.knowledge_base_id=? ORDER BY c.chunk_order", (knowledge_base_id,)).fetchall()
        return [Chunk.model_validate_json(row["payload"]) for row in rows]

    def save_recipe(self, recipe: Recipe) -> None:
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO recipes VALUES (?, ?, ?)", (recipe.recipe_id, recipe.model_dump_json(), utc_now()))

    def get_recipe(self, recipe_id: str) -> Recipe | None:
        with self._connect() as db:
            row = db.execute("SELECT payload FROM recipes WHERE recipe_id=?", (recipe_id,)).fetchone()
        return Recipe.model_validate_json(row["payload"]) if row else None

    def list_recipes(self) -> list[Recipe]:
        with self._connect() as db:
            rows = db.execute("SELECT payload FROM recipes ORDER BY created_at").fetchall()
        return [Recipe.model_validate_json(row["payload"]) for row in rows]

    def save_model(self, model_id: str, payload: dict[str, Any]) -> None:
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO model_registry VALUES (?, ?, ?)", (model_id, json.dumps(payload, ensure_ascii=False), utc_now()))

    def get_model(self, model_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT payload FROM model_registry WHERE model_id=?", (model_id,)).fetchone()
        return json.loads(row["payload"]) if row else None

    def list_models(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT payload FROM model_registry ORDER BY created_at").fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def save_scenario(self, scenario_id: str, payload: dict[str, Any]) -> None:
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO scenarios VALUES (?, ?, ?)", (scenario_id, json.dumps(payload, ensure_ascii=False), utc_now()))

    def list_scenarios(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT payload FROM scenarios ORDER BY created_at").fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def save_run(self, payload: dict[str, Any]) -> None:
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO runs VALUES (?, ?, ?)", (payload["run_id"], json.dumps(payload), utc_now()))

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT payload FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return json.loads(row["payload"]) if row else None

    def save_trace(self, event: TraceEvent) -> None:
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO trace_events VALUES (?, ?, ?)", (event.run_id, event.sequence, event.model_dump_json()))

    def list_trace(self, run_id: str) -> list[TraceEvent]:
        with self._connect() as db:
            rows = db.execute("SELECT payload FROM trace_events WHERE run_id=? ORDER BY sequence", (run_id,)).fetchall()
        return [TraceEvent.model_validate_json(row["payload"]) for row in rows]
