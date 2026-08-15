#!/usr/bin/env bash
# Bump the tracelayer version everywhere it is declared.
#
# Usage: contrib/bump-version.sh X.Y.Z
#
# Updates: pyproject.toml, src/tracelayer/__init__.py (the Python package
# version), and adapters/oh-my-pi/package.json (the omp extension package
# manifest). The omp manifest MUST stay in sync with the Python version —
# the CI drift test (test_source_adapter_is_valid_omp_plugin_package)
# fails otherwise. Run this before `git tag vX.Y.Z`.
#
# Uses python3 for the replacements (not sed) so the script is portable
# across BSD/macOS and GNU/Linux hosts.
set -euo pipefail

version="${1:?usage: contrib/bump-version.sh X.Y.Z}"

repo="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo"

python3 - "$version" <<'EOF'
import re
import sys
from pathlib import Path

version = sys.argv[1]
root = Path(".")

replacements = [
    (
        root / "pyproject.toml",
        re.compile(r'^version = ".*"', re.M),
        f'version = "{version}"',
    ),
    (
        root / "src" / "tracelayer" / "__init__.py",
        re.compile(r'^__version__ = ".*"', re.M),
        f'__version__ = "{version}"',
    ),
    (
        root / "adapters" / "oh-my-pi" / "package.json",
        re.compile(r'"version": ".*"'),
        f'"version": "{version}"',
    ),
]

for path, pattern, replacement in replacements:
    text = path.read_text(encoding="utf-8")
    if not pattern.search(text):
        raise SystemExit(f"{path}: version pattern not found — aborting")
    updated = pattern.sub(replacement, text, count=1)
    if updated != text:  # no-op when already at the target version
        path.write_text(updated, encoding="utf-8")
EOF

echo "version -> ${version} (pyproject.toml, __init__.py, adapters/oh-my-pi/package.json)"
