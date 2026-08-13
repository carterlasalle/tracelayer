"""Full-text search (FR-016, §Q search.py)."""

from __future__ import annotations

from tracelayer.graph.models import Node
from tracelayer.graph.store import GraphStore


def search(store: GraphStore, text: str, limit: int = 20) -> list[Node]:
    """Deterministic full-text search over trace ids, titles, symbols, and
    summaries.

    Delegates to the graph store's FTS5 index (with a LIKE fallback when FTS
    is disabled).  Embeddings are an optional plugin and never required for
    core search.
    """
    return store.search(text, limit=limit)
