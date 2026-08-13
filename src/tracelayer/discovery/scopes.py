"""Monorepo scope resolution (NFR-013)."""

from __future__ import annotations

from tracelayer.config import TraceConfig


def scope_of(rel_path: str, config: TraceConfig) -> str | None:
    """Return the scope name owning ``rel_path``, or None for the root scope.

    ``config.scopes`` maps scope names to path prefixes. A path belongs to a
    scope when it equals a prefix or starts with ``prefix/``; the scope whose
    matching prefix is longest wins, ties resolve to the lexicographically
    first scope name (deterministic).
    """
    p = rel_path.replace("\\", "/")
    best_name: str | None = None
    best_len = -1
    for name in sorted(config.scopes):
        for prefix in config.scopes[name]:
            prefix = prefix.replace("\\", "/").rstrip("/")
            if not prefix:
                continue
            if p == prefix or p.startswith(prefix + "/"):
                if len(prefix) > best_len:
                    best_name, best_len = name, len(prefix)
    return best_name
