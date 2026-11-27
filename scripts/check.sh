#!/usr/bin/env bash
# Runs locally what .github/workflows/ci.yml runs on pull requests and main.
# Stops with an error past CHECK_TIMEOUT_SECONDS (default 600, the same 10-minute limit as the CI
# job), so a hang fails instead of holding the machine; the run itself takes about 2 minutes.
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
# Ctrl-C reaches only the foreground group, so pass it (or a TERM) on to the checks.
trap 'kill -TERM -- "-$pid" 2>/dev/null; exit 130' INT TERM
limit="${CHECK_TIMEOUT_SECONDS:-600}"
start=$SECONDS
while kill -0 "$pid" 2>/dev/null; do
  if (( SECONDS - start >= limit )); then
    echo "check.sh: still running after ${limit}s, stopping it" >&2
    kill -TERM -- "-$pid" 2>/dev/null || true
    break
  fi
  sleep 1
done
status=0
wait "$pid" || status=$?
exit "$status"
