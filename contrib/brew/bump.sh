#!/usr/bin/env bash
# Regenerate contrib/brew/tracelayer.rb for a new release.
#
#   contrib/brew/bump.sh            # latest version on PyPI
#   contrib/brew/bump.sh 0.1.2      # specific version
#
# Requires: curl, jq, shasum (all present on stock macOS + Homebrew).

set -euo pipefail

version="${1:-}"
if [[ -z "$version" ]]; then
  version="$(curl -s https://pypi.org/pypi/tracelayer/json | jq -r .info.version)"
fi

formula="$(cd "$(dirname "$0")" && pwd)/tracelayer.rb"
meta="$(curl -s "https://pypi.org/pypi/tracelayer/${version}/json")"
url="$(printf '%s' "$meta" | jq -r '.urls[] | select(.packagetype == "sdist") | .url')"
sha256="$(curl -sL "$url" | shasum -a 256 | awk '{print $1}')"

python3 - "$formula" "$url" "$sha256" <<'PY'
import re
import sys

path, url, sha256 = sys.argv[1:]
text = open(path, encoding="utf-8").read()
text = re.sub(r'^  url .*$', f'  url "{url}"', text, count=1, flags=re.M)
text = re.sub(r'^  sha256 .*$', f'  sha256 "{sha256}"', text, count=1, flags=re.M)
open(path, "w", encoding="utf-8").write(text)
PY

echo "contrib/brew/tracelayer.rb -> $version"
grep -E '^  (version|url|sha256)' "$formula" || true
