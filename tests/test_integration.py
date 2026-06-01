"""
Integration tests — run against the real Graph API and (optionally) FUSE.

Skipped automatically if no credentials are stored on disk.
To authenticate for the first time:
    python mount.py

Run explicitly with:
    pytest -m integration -v
"""

import os
import time
import threading
import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------

def _credentials_available():
    """Return True only if a valid access token is stored on disk."""
    try:
        from Config import get_current_bearer
        return bool(get_current_bearer())
    except Exception:
        return False


def _fuse_accessible():
    return os.path.exists("/dev/fuse") and os.access("/dev/fuse", os.R_OK | os.W_OK)


def _require_credentials():
    """Skip the calling test if no valid OneDrive credentials are available."""
    if not _credentials_available():
        pytest.skip("No OneDrive credentials — run `python mount.py` once to authenticate")


def _require_fuse():
    """Skip the calling test if /dev/fuse is not accessible."""
    if not _fuse_accessible():
        pytest.skip("/dev/fuse not accessible — FUSE smoke test skipped")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def synced_index():
    """
    MetadataIndex loaded from the real on-disk DB and refreshed by one
    incremental sync against the live Graph API.
    Uses the real ~/.py-onedrive/ storage, same as the FUSE driver does.
    Skipped if no valid credentials are available.
    """
    _require_credentials()

    from MetadataIndex import MetadataIndex
    from Operations import sync_metadata

    index = MetadataIndex()
    index.load()
    sync_metadata(index)
    return index


# ---------------------------------------------------------------------------
# Graph API connectivity
# ---------------------------------------------------------------------------

def test_drive_endpoint_returns_200():
    """Bearer token is valid and the Graph API /me/drive endpoint is reachable."""
    _require_credentials()
    from GraphAPI import get as graph_get
    r = graph_get("https://graph.microsoft.com/v1.0/me/drive")
    assert r.status_code == 200
    body = r.json()
    assert "id" in body
    assert body.get("driveType") in ("personal", "business", "documentLibrary")


# ---------------------------------------------------------------------------
# Metadata sync
# ---------------------------------------------------------------------------

def test_sync_discovers_root(synced_index):
    """After sync, the root item ID is known."""
    assert synced_index.get_root_id() is not None


def test_sync_root_has_children(synced_index):
    """Root has at least one child (every OneDrive has a Documents folder)."""
    root_id = synced_index.get_root_id()
    children = synced_index.get_children(root_id)
    assert len(children) > 0, "Expected at least one item under OneDrive root"


def test_items_have_required_fuse_fields(synced_index):
    """Every item the FUSE driver will serve has id, name, and parentReference."""
    root_id = synced_index.get_root_id()
    for item in synced_index.get_children(root_id):
        assert "id" in item, f"missing 'id': {item}"
        assert "name" in item, f"missing 'name': {item}"
        assert "parentReference" in item, f"missing 'parentReference': {item}"


def test_inode_is_assigned_and_stable(synced_index):
    """Inode for an item is consistent across repeated lookups."""
    root_id = synced_index.get_root_id()
    children = synced_index.get_children(root_id)
    if not children:
        pytest.skip("No items under OneDrive root")

    item_id = children[0]["id"]
    inode_a = synced_index.get_inode(item_id)
    inode_b = synced_index.get_inode(item_id)
    assert inode_a == inode_b
    assert inode_a >= 2  # inode 1 is reserved for the FUSE root


def test_root_inode_roundtrip(synced_index):
    """ROOT_INODE (1) maps back to the root item ID."""
    from MetadataIndex import ROOT_INODE
    assert synced_index.get_id_for_inode(ROOT_INODE) == synced_index.get_root_id()


# ---------------------------------------------------------------------------
# Content download
# ---------------------------------------------------------------------------

def test_download_small_file(synced_index):
    """download_file fetches real bytes whose length matches the stored size."""
    from Operations import download_file

    target = _pick_small_file(synced_index, max_bytes=512 * 1024)  # ≤ 512 KB
    if target is None:
        pytest.skip("No file ≤ 512 KB found in the first two directory levels")

    content = download_file(target["id"])
    assert isinstance(content, bytes)
    assert len(content) > 0

    stored_size = target.get("size", 0)
    if stored_size:
        assert len(content) == stored_size, (
            f"'{target['name']}': expected {stored_size} bytes, got {len(content)}"
        )


# ---------------------------------------------------------------------------
# FUSE mount smoke test
# ---------------------------------------------------------------------------

def test_fuse_mount_lists_root(tmp_path, synced_index):
    """
    Mount the FUSE filesystem, verify that ls on the mountpoint returns
    exactly the names the MetadataIndex has under root, then unmount cleanly.
    """
    _require_fuse()
    import pyfuse3
    import trio
    from CacheManager import CacheManager
    from OneDriveFUSE import OneDriveFUSE
    from Globals import CACHE_FOLDER

    mountpoint = str(tmp_path / "mnt")
    os.makedirs(mountpoint)

    cache = CacheManager(CACHE_FOLDER)
    fs = OneDriveFUSE(synced_index, cache)

    options = set(pyfuse3.default_options)
    options.add("fsname=onedrive-integration-test")
    pyfuse3.init(fs, mountpoint, options)

    # Run pyfuse3.main (trio async) in a background thread so the test
    # thread can issue synchronous filesystem calls against the mount.
    errors = []

    def _fuse_thread():
        try:
            trio.run(pyfuse3.main)
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=_fuse_thread, daemon=True)
    thread.start()

    # Give the kernel a moment to register the mount
    time.sleep(0.3)

    assertion_error = None
    try:
        listed = sorted(os.listdir(mountpoint))
        root_id = synced_index.get_root_id()
        expected = sorted(item["name"] for item in synced_index.get_children(root_id))
        assert listed == expected, (
            f"FUSE ls = {listed}\nIndex   = {expected}"
        )
    except AssertionError as exc:
        assertion_error = exc
    finally:
        pyfuse3.close(unmount=True)
        thread.join(timeout=5)

    # Background thread errors after pyfuse3.close() are expected EBADF from
    # the forcibly closed fd — ignore them. Only fail on the ls assertion.
    if assertion_error:
        raise assertion_error


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pick_small_file(index, max_bytes, parent_id=None, depth=0):
    """Return the first regular file ≤ max_bytes within 2 directory levels."""
    if parent_id is None:
        parent_id = index.get_root_id()
    for item in index.get_children(parent_id):
        if "file" in item and 0 < item.get("size", 0) <= max_bytes:
            return item
    if depth < 2:
        for item in index.get_children(parent_id):
            if "folder" in item:
                found = _pick_small_file(index, max_bytes, item["id"], depth + 1)
                if found:
                    return found
    return None
