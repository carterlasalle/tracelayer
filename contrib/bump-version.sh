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
set -euo pipefail

version="${1:?usage: contrib/bump-version.sh X.Y.Z}"

repo="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo"

if ! grep -q "^version = \"${version}\"" pyproject.toml; then
    sed -i '' "s/^version = \".*\"/version = \"${version}\"/" pyproject.toml
fi
if ! grep -q "__version__ = \"${version}\"" src/tracelayer/__init__.py; then
    sed -i '' "s/__version__ = \".*\"/__version__ = \"${version}\"/" src/tracelayer/__init__.py
fi
if ! grep -q "\"version\": \"${version}\"" adapters/oh-my-pi/package.json; then
    sed -i '' "s/\"version\": \".*\"/\"version\": \"${version}\"/" adapters/oh-my-pi/package.json
fi

echo "version -> ${version} (pyproject.toml, __init__.py, adapters/oh-my-pi/package.json)"
