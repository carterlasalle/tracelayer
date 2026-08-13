"""File-level artifact extraction for files without section structure."""

from __future__ import annotations

from dataclasses import dataclass

from tracelayer.protocol import MarkerHit, iter_marker_hits


@dataclass
class FileLevelArtifact:
    path: str
    language: str | None
    markers: list[MarkerHit]


def extract_file_level(
    path: str, text: str, language: str | None
) -> FileLevelArtifact:
    """Attach all markers at file level (honest degradation, NFR-007).

    Used for every non-markdown non-yaml file and for JSON/TOML: no section
    precision is claimed. Markers are returned in line order.
    """
    return FileLevelArtifact(
        path=path, language=language, markers=list(iter_marker_hits(text, path))
    )
