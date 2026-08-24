from __future__ import annotations

import time
from typing import Any

from ..domain.models import TraceEvent
from ..store import Store


class TraceRecorder:
    def __init__(self, run_id: str, recipe_hash: str, store: Store):
        self.run_id = run_id
        self.recipe_hash = recipe_hash
        self.store = store
        self.events: list[TraceEvent] = []

    def record(self, node_id: str, status: str, summary: str, details: dict[str, Any] | None = None, started: float | None = None) -> TraceEvent:
        event = TraceEvent(run_id=self.run_id, node_id=node_id, sequence=len(self.events) + 1, status=status, summary=summary, duration_ms=round((time.perf_counter() - started) * 1000, 2) if started else 0.0, details=details or {})
        self.events.append(event)
        self.store.save_trace(event)
        return event

    def run_node(self, node_id: str, func, **kwargs: Any):
        started = time.perf_counter()
        self.record(node_id, "running", "节点正在运行")
        try:
            value = func(**kwargs)
            self.record(node_id, "completed", "节点运行完成", started=started)
            return value
        except Exception as exc:
            self.record(node_id, "failed", str(exc), details={"error_type": type(exc).__name__}, started=started)
            raise

