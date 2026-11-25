#!/usr/bin/env bash
# Runs locally what .github/workflows/ci.yml runs on pull requests and main.
set -euo pipefail
cd "$(dirname "$0")/.."

./scripts/selftest.sh "$PWD"
./scripts/install-capsulectl.sh
uv run --group browser playwright install chromium
HELLO_REQUIRE_BROWSER=1 uv run --group browser pytest -v
