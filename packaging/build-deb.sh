#!/usr/bin/env bash
# Build a .deb package for py-onedrive using fpm.
#
# Usage:
#   bash packaging/build-deb.sh [VERSION]
#
# Prerequisites (install once):
#   sudo apt install ruby ruby-dev rubygems build-essential
#   sudo gem install fpm
#
# Output: py-onedrive_<VERSION>_amd64.deb in the current directory.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${1:-0.1.0}"
ARCH="$(dpkg --print-architecture 2>/dev/null || echo amd64)"
PKGDIR="$(mktemp -d)"

cleanup() { rm -rf "$PKGDIR"; }
trap cleanup EXIT

echo "[build-deb] version=$VERSION arch=$ARCH"

# ── 1. Install Python deps + source into package tree ─────────────────────────
INSTALLDIR="$PKGDIR/usr/lib/py-onedrive"
mkdir -p "$INSTALLDIR"

pip3 install \
    -r "$REPO_ROOT/requirements.txt" \
    --target "$INSTALLDIR" \
    --quiet \
    --no-compile \
    --only-binary :all: 2>/dev/null || \
  pip3 install \
    -r "$REPO_ROOT/requirements.txt" \
    --target "$INSTALLDIR" \
    --quiet \
    --no-compile

# Copy Python source files (exclude tests and packaging dirs)
cp "$REPO_ROOT"/*.py "$INSTALLDIR/"

# Byte-compile for faster startup
python3 -m compileall -q "$INSTALLDIR"

# ── 2. Wrapper binary ──────────────────────────────────────────────────────────
mkdir -p "$PKGDIR/usr/bin"
cat > "$PKGDIR/usr/bin/py-onedrive" << 'EOF'
#!/usr/bin/env bash
export PYTHONPATH=/usr/lib/py-onedrive
exec python3 /usr/lib/py-onedrive/mount.py "$@"
EOF
chmod 755 "$PKGDIR/usr/bin/py-onedrive"

# ── 3. systemd user service ───────────────────────────────────────────────────
mkdir -p "$PKGDIR/usr/lib/systemd/user"
cp "$REPO_ROOT/packaging/py-onedrive.service" \
   "$PKGDIR/usr/lib/systemd/user/py-onedrive.service"

# ── 4. Build the .deb ─────────────────────────────────────────────────────────
cd "$REPO_ROOT"

fpm \
  --input-type dir \
  --output-type deb \
  --name py-onedrive \
  --version "$VERSION" \
  --architecture "$ARCH" \
  --description "OneDrive Files On-Demand FUSE client for Linux" \
  --url "https://github.com/ccampora/py-onedrive" \
  --maintainer "ccampora <ccampora@gmail.com>" \
  --license MIT \
  --depends python3 \
  --depends "fuse3 | fuse" \
  --depends libfuse3-3 \
  --after-install packaging/postinst \
  --chdir "$PKGDIR" \
  .

echo "[build-deb] Done: py-onedrive_${VERSION}_${ARCH}.deb"
