#!/usr/bin/env bash
# Install the TraceLayer agent skill into harness skill directories.
#
# Usage:
#   scripts/install-skill.sh [--claude] [--omp] [--repo DIR] [--link] [--force]
#
# Targets (repeatable):
#   --claude   ~/.claude/skills/traceability        (Claude Code)
#   --omp      ~/.omp/skills/traceability           (Oh My Pi; see note below)
#   --repo DIR DIR/.agents/skills/traceability      (repository-local)
#
# Options:
#   --link     symlink instead of copying (keeps updates in sync; dev use)
#   --force    replace an existing installation
#
# Defaults to --claude when no target is given. The Oh My Pi skill directory
# convention varies by install; adjust the OMP_SKILLS_DIR environment
# variable if yours differs (e.g. OMP_SKILLS_DIR=~/.omp/agent/skills).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$HERE/skills/traceability"

LINK=0
FORCE=0
TARGETS=()

usage() {
  sed -n '2,14p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit 2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --claude) TARGETS+=("$HOME/.claude/skills/traceability") ;;
    --omp) TARGETS+=("${OMP_SKILLS_DIR:-$HOME/.omp/skills}/traceability") ;;
    --repo)
      [[ $# -ge 2 ]] || usage
      TARGETS+=("$2/.agents/skills/traceability")
      shift
      ;;
    --link) LINK=1 ;;
    --force) FORCE=1 ;;
    -h|--help) usage ;;
    *) echo "unknown option: $1" >&2; usage ;;
  esac
  shift
done

if [[ ${#TARGETS[@]} -eq 0 ]]; then
  TARGETS+=("$HOME/.claude/skills/traceability")
fi

[[ -f "$SRC/SKILL.md" ]] || { echo "skill not found at $SRC" >&2; exit 1; }

for dst in "${TARGETS[@]}"; do
  if [[ -e "$dst" && "$FORCE" -eq 0 ]]; then
    echo "skip $dst (exists; use --force to replace)" >&2
    continue
  fi
  rm -rf "$dst"
  mkdir -p "$(dirname "$dst")"
  if [[ "$LINK" -eq 1 ]]; then
    ln -s "$SRC" "$dst"
  else
    cp -R "$SRC" "$dst"
  fi
  echo "installed $dst"
done
