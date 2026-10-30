#!/bin/sh
# Start Org A (port 8001) and Org B (port 8002) in one terminal; Ctrl-C stops both.
# Pass settings through the environment, e.g. PAYMENT=none ./scripts/run.sh
set -eu
uv run uvicorn hello.org_b:app --port 8002 --log-level warning &
B=$!
trap 'kill $B 2>/dev/null' EXIT INT TERM
echo "Org B (venue) on http://localhost:8002, Org A (booking) on http://localhost:8001: open http://localhost:8001"
uv run uvicorn hello.org_a:app --port 8001 --log-level warning
