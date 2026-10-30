#!/bin/sh
# Timed self-test of the README walkthrough: a fresh clone to hop 3, with payments off.
# Fails if it takes longer than 15 minutes. Usage: scripts/selftest.sh [REPO_URL_OR_PATH]
set -eu
SRC="${1:-https://github.com/action-state-group/cross-org-agents-hello-world.git}"
WORK="$(mktemp -d)"
START=$(date +%s)
step() { echo "[$(( $(date +%s) - START ))s] $*"; }

step "clone"
git clone -q "$SRC" "$WORK/repo"
cd "$WORK/repo"
step "install capsulectl"
./scripts/install-capsulectl.sh
step "setup"
uv run scripts/setup.py
step "start both orgs (PAYMENT=none)"
PAYMENT=none ./scripts/run.sh > "$WORK/run.log" 2>&1 &
RUN=$!
trap 'kill $RUN 2>/dev/null; pkill -f "$WORK/repo/.venv" 2>/dev/null; rm -rf "$WORK"' EXIT
for _ in $(seq 1 120); do curl -fs -o /dev/null http://localhost:8001/ && curl -fs -o /dev/null http://localhost:8002/docs && break; sleep 1; done
step "hop 1 and 2: book"
PAGE="$(curl -fsS -X POST http://localhost:8001/book -d venue=Hall+3 -d date=2026-11-04 -d people=40)"
HOP3="$(printf '%s' "$PAGE" | grep -o 'http://localhost:8002/verify#set=[A-Za-z0-9-]*' | head -1)"
test -n "$HOP3" || { echo "no hop 3 link"; exit 1; }
step "hop 3: $HOP3"
curl -fsS -o /dev/null http://localhost:8002/verify
curl -fsS "http://localhost:8002/sets/${HOP3##*set=}" | grep -q '"records"'
ELAPSED=$(( $(date +%s) - START ))
step "done in ${ELAPSED}s (limit 900s); open the hop 3 link in a browser to see it verify"
test "$ELAPSED" -le 900
