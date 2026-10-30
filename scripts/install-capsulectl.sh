#!/bin/sh
# Download a pinned capsulectl release into ./bin and check it against the release's SHA256SUMS.
set -eu
VERSION="${CAPSULECTL_VERSION:-v0.1.0-rc24}"
OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
ARCH="$(uname -m)"
case "$ARCH" in x86_64|amd64) ARCH=amd64 ;; arm64|aarch64) ARCH=arm64 ;; esac
NAME="capsulectl-$VERSION-$OS-$ARCH"
BASE="https://github.com/action-state-group/capsule-cli/releases/download/$VERSION"
mkdir -p bin
if ! curl -fsSL -o "bin/$NAME" "$BASE/$NAME"; then
  echo "No prebuilt capsulectl for $OS-$ARCH. Build it with Go 1.24+ instead:" >&2
  echo "  GOBIN=\$PWD/bin go install github.com/action-state-group/capsule-cli/cmd/capsulectl@$VERSION" >&2
  exit 1
fi
curl -fsSL -o bin/SHA256SUMS "$BASE/SHA256SUMS"
(cd bin && grep " $NAME\$" SHA256SUMS | { command -v sha256sum >/dev/null && sha256sum -c - || shasum -a 256 -c -; })
mv "bin/$NAME" bin/capsulectl
chmod +x bin/capsulectl
rm bin/SHA256SUMS
bin/capsulectl --version
