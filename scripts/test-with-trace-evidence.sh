#!/usr/bin/env bash
# Run the test suite with machine-readable results and coverage, then print
# the artifact paths consumed by the trace evidence ingest step
# (see .github/workflows/trace.yml).
#
# Artifacts:
#   junit.xml      - JUnit XML test results (--junitxml)
#   coverage.xml   - Cobertura coverage report (--cov-report=xml)
#
# The script always exits with pytest's exit code so CI fails when tests
# fail, independent of trace enforcement.
set -eu

uv run pytest --junitxml=junit.xml --cov=src --cov-report=xml "$@"

echo "Trace evidence artifacts:"
echo "  junit.xml     (JUnit test results)"
echo "  coverage.xml  (Cobertura coverage)"
