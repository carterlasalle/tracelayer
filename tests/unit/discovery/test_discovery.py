"""Discovery unit tests: classification, include/exclude globs, gitignore,
symlink confinement, binary/size safety, deterministic ordering, and scope
resolution."""

from __future__ import annotations

import os
from pathlib import Path

from tests.conftest import make_git_repo
from tracelayer.config import DiscoveryConfig, IndexConfig, TraceConfig
from tracelayer.discovery.files import (
    MAX_FILE_BYTES,
    SourceFile,
    classify,
    discover_files,
    read_text_safe,
)
from tracelayer.discovery.ignore import build_ignored, glob_match
from tracelayer.discovery.scopes import scope_of
from tracelayer.git.repo import GitRepo


# trace:v1 id=test.dogfood.tests.unit.discovery.test_discovery.py type=test
def _write(root: Path, rel: str, content: str | bytes) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        p.write_bytes(content)
    else:
        p.write_text(content, encoding="utf-8")


def _paths(files: list[SourceFile]) -> list[str]:
    return [str(sf.path) for sf in files]


def test_classify_by_extension() -> None:
    assert classify(Path("a.py")) == ("source", "python")
    assert classify(Path("a.pyi")) == ("source", "python")
    assert classify(Path("a.ts")) == ("source", "typescript")
    assert classify(Path("a.js")) == ("source", "javascript")
    assert classify(Path("a.go")) == ("source", "go")
    assert classify(Path("a.rs")) == ("source", "rust")
    assert classify(Path("a.java")) == ("source", "java")
    assert classify(Path("a.cpp")) == ("source", "cpp")
    assert classify(Path("a.md")) == ("doc", "markdown")
    assert classify(Path("a.yaml")) == ("config", "yaml")
    assert classify(Path("a.json")) == ("config", "json")
    assert classify(Path("a.toml")) == ("config", "toml")
    assert classify(Path("a.csv")) == ("data", "text")
    assert classify(Path("a.sh")) == ("source", "shell")
    assert classify(Path("README")) == ("other", None)  # no extension
    assert classify(Path("a.xyz")) == ("other", None)
    # Case-insensitive.
    assert classify(Path("A.PY")) == ("source", "python")


def test_discover_files_deterministic_order(tmp_path: Path) -> None:
    _write(tmp_path, "b.py", "x")
    _write(tmp_path, "a/2.py", "x")
    _write(tmp_path, "a/1.py", "x")
    _write(tmp_path, "c.md", "x")
    config = TraceConfig(repo_id="r")
    files = discover_files(tmp_path, config)
    assert _paths(files) == ["a/1.py", "a/2.py", "b.py", "c.md"]
    # Deterministic: a second pass yields the same order.
    assert _paths(discover_files(tmp_path, config)) == _paths(files)
    kinds = {sf.path.as_posix(): sf.kind for sf in files}
    assert kinds["b.py"] == "source"
    assert kinds["c.md"] == "doc"
    langs = {sf.path.as_posix(): sf.language for sf in files}
    assert langs["b.py"] == "python"
    assert langs["c.md"] == "markdown"


def test_discover_include_globs(tmp_path: Path) -> None:
    _write(tmp_path, "src/a.py", "x")
    _write(tmp_path, "src/b.py", "x")
    _write(tmp_path, "tests/test_a.py", "x")
    _write(tmp_path, "README.md", "x")
    config = TraceConfig(repo_id="r", discovery=DiscoveryConfig(include=["src/**"]))
    files = discover_files(tmp_path, config)
    assert _paths(files) == ["src/a.py", "src/b.py"]


def test_discover_exclude_globs(tmp_path: Path) -> None:
    _write(tmp_path, "src/a.py", "x")
    _write(tmp_path, "src/generated/b.py", "x")
    _write(tmp_path, "build/x.o", "x")
    config = TraceConfig(
        repo_id="r", discovery=DiscoveryConfig(exclude=["src/generated/**", "build/**"])
    )
    files = discover_files(tmp_path, config)
    assert _paths(files) == ["src/a.py"]


def test_discover_respects_gitignore(tmp_path: Path) -> None:
    root = make_git_repo(
        tmp_path,
        {
            ".gitignore": "*.log\ncache/\n",
            "keep.py": "x",
            "noise.log": "x",
            "cache/data.json": "x",
        },
    )
    git_repo = GitRepo.open(root)
    assert git_repo is not None
    config = TraceConfig(repo_id="r", index=IndexConfig(respect_gitignore=True))
    files = discover_files(root, config, git_repo)
    # noise.log and cache/data.json are gitignored; the tracked .gitignore
    # file itself is not subject to ignore rules, so it is discovered.
    assert _paths(files) == [".gitignore", "keep.py"]
    # With gitignore disabled, ignored files are discovered.
    config2 = TraceConfig(repo_id="r", index=IndexConfig(respect_gitignore=False))
    files2 = discover_files(root, config2, git_repo)
    assert set(_paths(files2)) == {".gitignore", "cache/data.json", "keep.py", "noise.log"}


def test_always_ignored_dotgit_and_cache(tmp_path: Path) -> None:
    _write(tmp_path, "src/a.py", "x")
    _write(tmp_path, ".git/config", "x")  # fake .git dir
    _write(tmp_path, ".trace/cache/index.sqlite3", "x")
    config = TraceConfig(repo_id="r", index=IndexConfig(respect_gitignore=False))
    files = discover_files(tmp_path, config)
    # .git/** and .trace/cache/** are always excluded, even without git.
    assert _paths(files) == ["src/a.py"]


def test_discover_skips_symlink_outside_root(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    _write(outside, "secret.py", "x")
    root = tmp_path / "repo"
    root.mkdir()
    _write(root, "keep.py", "x")
    os.symlink(outside / "secret.py", root / "leak.py")  # file symlink out
    config = TraceConfig(repo_id="r")
    files = discover_files(root, config)
    assert _paths(files) == ["keep.py"]
    # A symlink resolving inside the root is kept.
    os.symlink(root / "keep.py", root / "alias.py")
    files2 = discover_files(root, config)
    assert _paths(files2) == ["alias.py", "keep.py"]


def test_discover_never_follows_symlinked_directories(tmp_path: Path) -> None:
    real = tmp_path / "real"
    _write(real, "a.py", "x")
    root = tmp_path / "repo"
    root.mkdir()
    os.symlink(real, root / "linkdir")
    config = TraceConfig(repo_id="r")
    files = discover_files(root, config)
    assert _paths(files) == []  # symlinked dir is not descended into


def test_discover_skips_large_files(tmp_path: Path) -> None:
    _write(tmp_path, "big.bin", b"x" * (MAX_FILE_BYTES + 1))
    _write(tmp_path, "small.py", "x")
    config = TraceConfig(repo_id="r")
    files = discover_files(tmp_path, config)
    assert _paths(files) == ["small.py"]


def test_read_text_safe(tmp_path: Path) -> None:
    p = tmp_path / "a.py"
    p.write_text("print(1)\n", encoding="utf-8")
    assert read_text_safe(p) == "print(1)\n"

    binary = tmp_path / "b.bin"
    binary.write_bytes(b"\x00\x01\xff\xfe")
    assert read_text_safe(binary) is None  # non-UTF-8 -> None

    big = tmp_path / "big.py"
    big.write_bytes(b"x" * (MAX_FILE_BYTES + 1))
    assert read_text_safe(big) is None  # size guard

    assert read_text_safe(tmp_path / "missing.py") is None


def test_generated_files_still_discovered(tmp_path: Path) -> None:
    _write(tmp_path, "src/gen.py", "x")
    config = TraceConfig(repo_id="r", discovery=DiscoveryConfig(generated=["src/gen.py"]))
    files = discover_files(tmp_path, config)
    # generated files are returned (caller flags them), not excluded.
    assert _paths(files) == ["src/gen.py"]


def test_build_ignored_covers_exclude_and_git(tmp_path: Path) -> None:
    root = make_git_repo(tmp_path, {".gitignore": "*.log\n", "a.txt": "x"})
    git_repo = GitRepo.open(root)
    assert git_repo is not None
    config = TraceConfig(repo_id="r", discovery=DiscoveryConfig(exclude=["vendor/**"]))
    is_ignored = build_ignored(root, config, git_repo)
    assert is_ignored("vendor/lib.py") is True  # config exclude
    assert is_ignored("a.log") is True  # gitignore
    assert is_ignored("a.txt") is False
    assert is_ignored(".git/HEAD") is True  # always
    assert is_ignored(".trace/cache/x") is True  # always


def test_glob_match_semantics() -> None:
    assert glob_match("src/a.py", ["src/**"]) is True
    assert glob_match("a.py", ["src/**"]) is False
    assert glob_match("src/a.py", ["**/*"]) is True
    assert glob_match("src/deep/a.py", ["src/**"]) is True
    assert glob_match("src/a.py", ["src/*"]) is True
    assert glob_match("src/deep/a.py", ["src/*"]) is False  # * doesn't cross /
    assert glob_match("a.py", ["*.py"]) is True
    assert glob_match("dir/a.py", ["*.py"]) is False
    assert glob_match("a.py", ["?.py"]) is True
    assert glob_match("ab.py", ["?.py"]) is False


def test_scope_of_longest_prefix_match() -> None:
    config = TraceConfig(
        repo_id="r",
        scopes={
            "core": ["src/core"],
            "lib": ["src/lib", "vendor/lib"],
            "nested": ["src/a/b"],
            "shallow": ["src/a"],
        },
    )
    assert scope_of("src/core/x.py", config) == "core"
    assert scope_of("vendor/lib/a.py", config) == "lib"
    # Longest prefix wins: src/a/b beats src/a.
    assert scope_of("src/a/b/c.py", config) == "nested"
    assert scope_of("src/a/plain.py", config) == "shallow"
    assert scope_of("docs/x.md", config) is None
    # Prefix equality matches.
    assert scope_of("src/core", config) == "core"
    # Windows separators normalized.
    assert scope_of("src\\core\\x.py", config) == "core"
