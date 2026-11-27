#!/usr/bin/env bash
# Runs locally what .github/workflows/ci.yml runs on pull requests and main.
# Stops with exit 124 within CHECK_TIMEOUT_SECONDS (default 600, the same 10-minute limit as the CI
# job), so a hang fails instead of holding the machine; the run itself takes about 1 minute.
set -euo pipefail
cd "$(dirname "$0")/.."

checks() {
  ./scripts/selftest.sh "$PWD"
  ./scripts/install-capsulectl.sh
  uv run --group browser playwright install chromium
  HELLO_REQUIRE_BROWSER=1 uv run --group browser pytest -v
}

# Job control puts the checks in their own process group, so stopping that group stops every
# process they started (the org servers included) and nothing outside it.
set -m
checks &
pid=$!
set +m  # only the checks need a group of their own; the polling below runs in ours

# TERM the checks' group, give it GRACE seconds, then KILL whatever is left, so nothing
# outlives this script even if it ignores TERM.
GRACE=10
stop() {
  kill -TERM -- "-$pid" 2>/dev/null || return 0
  local waited=0
  while kill -0 -- "-$pid" 2>/dev/null && (( waited < GRACE )); do sleep 1; waited=$((waited + 1)); done
  kill -KILL -- "-$pid" 2>/dev/null || true
}
# Ctrl-C reaches only the foreground group, so pass it (or a TERM) on to the checks.
trap 'stop; exit 130' INT TERM

# The grace period comes out of the limit, so the whole run ends within it.
limit="${CHECK_TIMEOUT_SECONDS:-600}"
start=$SECONDS
while kill -0 "$pid" 2>/dev/null; do
  if (( SECONDS - start >= limit - GRACE )); then
    echo "check.sh: still running after $((SECONDS - start))s, stopping it" >&2
    stop
    wait "$pid" 2>/dev/null || true
    exit 124
  fi
  sleep 1
done
status=0
wait "$pid" || status=$?
exit "$status"
