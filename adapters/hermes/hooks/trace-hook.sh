#!/usr/bin/env bash
# TraceLayer hook adapter for Hermes Agent (Nous Research).
# Usage: trace-hook.sh <event> [hermes]
# Payload arrives via $CLAUDE_TOOL_INPUT (JSON) or stdin; paths via
# $CLAUDE_FILE_PATHS. Exit 2 blocks the tool call (Hermes contract).
set -uo pipefail

EVENT="${1:-}"
[ -n "$EVENT" ] || { echo "usage: $0 <event> [hermes]" >&2; exit 2; }

PAYLOAD="${CLAUDE_TOOL_INPUT:-}"
if [ -z "$PAYLOAD" ]; then
  PAYLOAD="$(cat 2>/dev/null || true)"
fi

# Normalize: derive `path` from payload or CLAUDE_FILE_PATHS; pin session id.
PAYLOAD="$(
  CLAUDE_FILE_PATHS="${CLAUDE_FILE_PATHS:-}" python3 - "$PAYLOAD" <<'PY'
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
  echo "$REASON" >&2
  exit 2
fi

printf '%s' "$OUT" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
    print(d.get("output", ""), end="")
except Exception:
    pass'
exit 0
