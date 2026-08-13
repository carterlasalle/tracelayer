#!/usr/bin/env bash
# TraceLayer hook adapter for OpenAI Codex CLI.
# Usage: trace-hook.sh <event> [codex]
# Reads the Codex hook JSON payload from stdin, runs the deterministic trace
# hook, and emits the Codex contract: {"decision":"deny","reason":"..."} for
# blocked events, bounded output text otherwise.
set -uo pipefail

EVENT="${1:-}"
[ -n "$EVENT" ] || { echo "usage: $0 <event> [codex]" >&2; exit 2; }

PAYLOAD="$(cat 2>/dev/null || true)"
if [ -z "$PAYLOAD" ] && [ -n "${CLAUDE_TOOL_INPUT:-}" ]; then
  PAYLOAD="$CLAUDE_TOOL_INPUT"
fi

# Normalize: derive `path` from tool_input for file tools and pin session id.
PAYLOAD="$(
  python3 - "$PAYLOAD" <<'PY'
import json, os, sys
raw = sys.argv[1]
d = json.loads(raw) if raw else {}
if d.get("hook_event_name") in ("PreToolUse", "PostToolUse") and "path" not in d:
    ti = d.get("tool_input") or {}
    for key in ("path", "file_path", "filePath"):
        if ti.get(key):
            d["path"] = ti[key]
            break
    if "path" not in d:
        files = os.environ.get("CLAUDE_FILE_PATHS", "").split()
        if files:
            d["path"] = files[0]
d.setdefault("session_id", os.environ.get("TRACE_SESSION", "default"))
print(json.dumps(d))
PY
)"

OUT="$(printf '%s' "$PAYLOAD" | uv run trace hook "$EVENT" --format json 2>/dev/null)"
RC=$?

if [ "$RC" -eq 1 ]; then
  REASON="$(printf '%s' "$OUT" | python3 -c '
import json, sys
try:
    print(json.load(sys.stdin).get("output", "blocked by trace policy"))
except Exception:
    print("blocked by trace policy")
')"
  REASON_JSON="$(python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))' <<<"$REASON")"
  printf '{"decision":"deny","reason":%s}' "$REASON_JSON"
  exit 0
fi

printf '%s' "$OUT" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
    print(d.get("output", ""), end="")
except Exception:
    pass'
exit 0
