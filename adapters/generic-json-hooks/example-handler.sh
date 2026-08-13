#!/usr/bin/env bash
# Reference handler for the generic JSON hooks protocol.
#
# Reads one JSON object {event, payload, session_id} from stdin, dispatches
# to `uv run trace hook <event> --format json`, and relays the JSON decision
# to stdout unchanged.
#
# Contract (adapters/generic-json-hooks/protocol.md):
#   - stdout: {"decision": "allow"|"block", "output": "..."}
#   - exit 0 for every non-stop event;
#   - exit 0 when the stop gate passes, exit 1 when it blocks.
#
# The event name is taken from the harness (argv or stdin JSON); it is never
# interpolated into a shell — the whitelist below guarantees safety (T2).

set -u

ALLOWED_EVENTS="session-start prompt-context pre-mutation post-mutation post-batch stop"

# Buffer stdin so the payload survives event extraction and can be passed
# through to `trace hook` verbatim.
INPUT=$(cat)

# Event: argv[1] wins, otherwise the "event" key of the stdin JSON.
EVENT="${1:-}"
if [ -z "${EVENT}" ]; then
  EVENT=$(printf '%s' "${INPUT}" | sed -n 's/.*"event"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
fi

case " ${ALLOWED_EVENTS} " in
  *" ${EVENT} "*)
    ;;
  *)
    printf '%s\n' '{"decision": "allow", "output": "trace hook: unknown event"}' >&2
    exit 0
    ;;
esac

# Pass the original envelope through; trace hook <event> --format json reads
# the same {event, payload, session_id} object from stdin.
printf '%s' "${INPUT}" | uv run trace hook "${EVENT}" --format json
exit $?
