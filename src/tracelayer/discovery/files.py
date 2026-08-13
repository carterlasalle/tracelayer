"""Repository file discovery: classification, safe reads, enumeration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from tracelayer.config import TraceConfig
from tracelayer.discovery.ignore import build_ignored, glob_match

MAX_FILE_BYTES = 2 * 1024 * 1024  # skip larger files (Threat T9 safeguard)

# Extension -> (kind, language); unknown extensions classify as ("other", None).
_EXT_KIND_LANG: dict[str, tuple[str, str | None]] = {
    ".py": ("source", "python"),
    ".pyi": ("source", "python"),
    ".ts": ("source", "typescript"),
    ".tsx": ("source", "typescript"),
    ".mts": ("source", "typescript"),
    ".cts": ("source", "typescript"),
    ".js": ("source", "javascript"),
    ".jsx": ("source", "javascript"),
    ".mjs": ("source", "javascript"),
    ".cjs": ("source", "javascript"),
    ".go": ("source", "go"),
    ".rs": ("source", "rust"),
    ".java": ("source", "java"),
    ".c": ("source", "c"),
    ".h": ("source", "c"),
    ".cpp": ("source", "cpp"),
    ".cc": ("source", "cpp"),
    ".cxx": ("source", "cpp"),
    ".hpp": ("source", "cpp"),
    ".sh": ("source", "shell"),
    ".bash": ("source", "shell"),
    ".zsh": ("source", "shell"),
    ".sql": ("source", "sql"),
    ".yaml": ("config", "yaml"),
    ".yml": ("config", "yaml"),
    ".json": ("config", "json"),
    ".toml": ("config", "toml"),
    ".md": ("doc", "markdown"),
    ".markdown": ("doc", "markdown"),
    ".rst": ("doc", "text"),
    ".txt": ("doc", "text"),
    ".csv": ("data", "text"),
    ".tsv": ("data", "text"),
    ".jsonl": ("data", "json"),
}


@dataclass
class SourceFile:
    path: Path  # relative to root
    kind: str  # "source"|"doc"|"config"|"data"|"other"
    language: str | None  # python|typescript|javascript|go|rust|java|c|cpp|
    # yaml|json|toml|markdown|shell|sql|text|None


def classify(path: Path) -> tuple[str, str | None]:
    """Return (kind, language) for a file path, purely by extension.

    Extension-less files and unknown extensions classify as ("other", None);
    ``.h`` maps to C (documented choice).
    """
    kind, lang = _EXT_KIND_LANG.get(path.suffix.lower(), ("other", None))
    return kind, lang


def read_text_safe(path: Path) -> str | None:
    """Read a file as UTF-8 text; None on binary content, size, or I/O errors.

    Files larger than MAX_FILE_BYTES or containing non-UTF-8 bytes are treated
    as unreadable so callers skip them instead of failing the index.
    """
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) > MAX_FILE_BYTES:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def discover_files(
    root: Path, config: TraceConfig, git_repo: object | None = None
) -> list[SourceFile]:
    """Enumerate files under ``root`` per discovery config, sorted by path.

    Deterministic order (sorted relative paths). Applies
    ``config.discovery.include``/``exclude`` globs, gitignore via
    ``git_repo.is_ignored`` when ``config.index.respect_gitignore``, skips
    symlinks resolving outside ``root``, never follows symlinked directories,
    and omits files larger than MAX_FILE_BYTES. ``config.discovery.generated``
    files are still returned; callers flag them as generated.
    """
    root_resolved = Path(root).resolve()
    is_ignored = build_ignored(root_resolved, config, git_repo)
    includes = list(config.discovery.include)
    files: list[SourceFile] = []
    for dirpath, dirnames, filenames in os.walk(root_resolved, followlinks=False):
        kept_dirs: list[str] = []
        for d in sorted(dirnames):
            full_dir = Path(dirpath) / d
            rel_dir = os.path.relpath(full_dir, root_resolved).replace(os.sep, "/")
            if full_dir.is_symlink():
                continue  # never follow directory symlinks
            if is_ignored(rel_dir + "/"):
                continue
            kept_dirs.append(d)
        dirnames[:] = kept_dirs
        for fname in sorted(filenames):
            full = Path(dirpath) / fname
            rel = os.path.relpath(full, root_resolved).replace(os.sep, "/")
            if is_ignored(rel):
                continue
            if full.is_symlink() and not _within(full.resolve(), root_resolved):
                continue
            if includes and not glob_match(rel, includes):
                continue
            try:
                if full.stat().st_size > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            kind, lang = classify(full)
            files.append(SourceFile(path=Path(rel), kind=kind, language=lang))
    files.sort(key=lambda sf: str(sf.path))
    return files


def _within(resolved: Path, root: Path) -> bool:
    """True when ``resolved`` is ``root`` itself or lies under it."""
    try:
        resolved.relative_to(root)
        return True
    except ValueError:
        return False
