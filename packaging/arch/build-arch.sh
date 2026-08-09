#!/usr/bin/env bash
# Build an Arch package for py-onedrive and publish it to the local pacman
# repo at /srv/pkgrepo.
#
# Usage:
#   bash packaging/arch/build-arch.sh [VERSION]
#
# VERSION defaults to the pkgver already set in PKGBUILD. Pass a version to
# bump PKGBUILD to a new git tag (e.g. after `git tag vX.Y.Z && git push --tags`
# triggers the GitHub release build for the .deb).
#
# The repo lives outside $HOME because pacman's sandboxed download user
# (DownloadUser=alpm in pacman.conf) can't traverse a mode-750 home directory
# to reach a file:// repo under it.
#
# Requires: base-devel (makepkg), pacman (repo-add)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ARCH_DIR="$REPO_ROOT/packaging/arch"
LOCAL_REPO="${PKGREPO_DIR:-/srv/pkgrepo}"
REPO_NAME="py-onedrive-local"

if [[ $# -ge 1 ]]; then
    VERSION="$1"
    sed -i "s/^pkgver=.*/pkgver=$VERSION/" "$ARCH_DIR/PKGBUILD"
    sed -i "s/^pkgrel=.*/pkgrel=1/" "$ARCH_DIR/PKGBUILD"
fi

VERSION="$(grep -m1 '^pkgver=' "$ARCH_DIR/PKGBUILD" | cut -d= -f2)"
echo "[build-arch] version=$VERSION"

mkdir -p "$LOCAL_REPO"

# ── 1. Fetch source tarball and compute its checksum ──────────────────────────
cd "$ARCH_DIR"
TARBALL_URL="https://github.com/ccampora/py-onedrive/archive/refs/tags/v${VERSION}.tar.gz"
TMP_TARBALL="$(mktemp)"
trap 'rm -f "$TMP_TARBALL"' EXIT

echo "[build-arch] fetching $TARBALL_URL"
curl -fsSL "$TARBALL_URL" -o "$TMP_TARBALL"
SHA256="$(sha256sum "$TMP_TARBALL" | cut -d' ' -f1)"
sed -i "s/^sha256sums=.*/sha256sums=('$SHA256')/" "$ARCH_DIR/PKGBUILD"

# ── 2. Build the package ───────────────────────────────────────────────────────
makepkg -f --clean --nodeps --noconfirm

mapfile -t PKGFILES < <(makepkg --packagelist)
PKGFILE="${PKGFILES[0]}"

# ── 3. Publish to the local repo ───────────────────────────────────────────────
cp "$PKGFILE" "$LOCAL_REPO/"
repo-add "$LOCAL_REPO/$REPO_NAME.db.tar.gz" "$LOCAL_REPO/$(basename "$PKGFILE")"

echo "[build-arch] Done: $(basename "$PKGFILE") published to $LOCAL_REPO"
