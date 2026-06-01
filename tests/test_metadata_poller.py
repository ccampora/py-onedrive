import pytest
import trio
from unittest.mock import patch, MagicMock, AsyncMock, call

import pyfuse3

from MetadataIndex import MetadataIndex, ROOT_INODE
from CacheManager import CacheManager
from OneDriveFUSE import OneDriveFUSE


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

ROOT_ID = "DRIVE!root"

FOLDER_ITEM = {
    "id": "DRIVE!folder1",
    "name": "Documents",
    "parentReference": {"id": ROOT_ID, "path": "/drive/root:"},
    "folder": {"childCount": 1},
    "size": 0,
    "eTag": '"etag-folder"',
    "lastModifiedDateTime": "2024-01-01T00:00:00Z",
}

FILE_ITEM = {
    "id": "DRIVE!file1",
    "name": "notes.txt",
    "parentReference": {"id": "DRIVE!folder1", "path": "/drive/root:/Documents"},
    "file": {"mimeType": "text/plain"},
    "size": 512,
    "eTag": '"etag-file1"',
    "lastModifiedDateTime": "2024-01-01T00:00:00Z",
}


@pytest.fixture
def index(tmp_path):
    db = tmp_path / "db"
    db.mkdir()
    inode_file = tmp_path / ".inodes"
    with (
        patch("MetadataIndex.ONEDRIVE_DB_FOLDER", str(db)),
        patch("MetadataIndex.INODE_FILE", str(inode_file)),
    ):
        idx = MetadataIndex()
        idx.upsert(FOLDER_ITEM)
        idx.upsert(FILE_ITEM)
        idx._root_id = ROOT_ID
        idx._id_to_inode[ROOT_ID] = ROOT_INODE
        idx._inode_to_id[ROOT_INODE] = ROOT_ID
        yield idx


@pytest.fixture
def cache(tmp_path):
    return CacheManager(cache_dir=str(tmp_path / "cache"))


@pytest.fixture
def fuse(index, cache):
    return OneDriveFUSE(index, cache)


# ---------------------------------------------------------------------------
# _poll_once — nothing changed
# ---------------------------------------------------------------------------

def test_poll_once_nothing_changed_does_not_invalidate(fuse):
    with (
        patch("OneDriveFUSE.sync_metadata", return_value=[]),
        patch("OneDriveFUSE.pyfuse3.invalidate_inode") as mock_inv,
    ):
        trio.run(fuse._poll_once)
    mock_inv.assert_not_called()


# ---------------------------------------------------------------------------
# _poll_once — items changed
# ---------------------------------------------------------------------------

def test_poll_once_invalidates_kernel_inode_for_each_changed_item(fuse, index):
    folder_inode = index.get_inode(FOLDER_ITEM["id"])
    file_inode = index.get_inode(FILE_ITEM["id"])

    with (
        patch("OneDriveFUSE.sync_metadata",
              return_value=[FOLDER_ITEM["id"], FILE_ITEM["id"]]),
        patch("OneDriveFUSE.pyfuse3.invalidate_inode") as mock_inv,
    ):
        trio.run(fuse._poll_once)

    invalidated_inodes = {c.args[0] for c in mock_inv.call_args_list}
    assert folder_inode in invalidated_inodes
    assert file_inode in invalidated_inodes


def test_poll_once_passes_attr_only_false(fuse, index):
    with (
        patch("OneDriveFUSE.sync_metadata", return_value=[FILE_ITEM["id"]]),
        patch("OneDriveFUSE.pyfuse3.invalidate_inode") as mock_inv,
    ):
        trio.run(fuse._poll_once)

    _, kwargs = mock_inv.call_args
    assert kwargs.get("attr_only", True) is False


def test_poll_once_evicts_cache_for_changed_items(fuse, index, cache):
    # Seed the cache with a file
    cache.put(FILE_ITEM["id"], FILE_ITEM["eTag"], b"cached content")
    assert cache.get(FILE_ITEM["id"], FILE_ITEM["eTag"]) is not None

    with (
        patch("OneDriveFUSE.sync_metadata", return_value=[FILE_ITEM["id"]]),
        patch("OneDriveFUSE.pyfuse3.invalidate_inode"),
    ):
        trio.run(fuse._poll_once)

    assert cache.get(FILE_ITEM["id"], FILE_ITEM["eTag"]) is None


# ---------------------------------------------------------------------------
# _poll_once — deleted items
# ---------------------------------------------------------------------------

def test_poll_once_handles_deleted_item(fuse, index, cache):
    """
    Deleted items are removed from the index by sync_metadata but their
    inode is still in the mapping — _poll_once must invalidate it.
    """
    file_inode = index.get_inode(FILE_ITEM["id"])
    cache.put(FILE_ITEM["id"], FILE_ITEM["eTag"], b"data")

    # sync_metadata already called index.delete() before returning changed_ids
    index.delete(FILE_ITEM["id"])

    with (
        patch("OneDriveFUSE.sync_metadata", return_value=[FILE_ITEM["id"]]),
        patch("OneDriveFUSE.pyfuse3.invalidate_inode") as mock_inv,
    ):
        trio.run(fuse._poll_once)

    invalidated = {c.args[0] for c in mock_inv.call_args_list}
    assert file_inode in invalidated
    assert cache.get(FILE_ITEM["id"], FILE_ITEM["eTag"]) is None


# ---------------------------------------------------------------------------
# _poll_once — kernel invalidation errors are swallowed
# ---------------------------------------------------------------------------

def test_poll_once_continues_when_invalidate_inode_raises(fuse, index, cache):
    """
    pyfuse3.invalidate_inode can fail if the FS is unmounting.
    The poller must not crash — it should continue and still evict the cache.
    """
    cache.put(FILE_ITEM["id"], FILE_ITEM["eTag"], b"data")

    with (
        patch("OneDriveFUSE.sync_metadata", return_value=[FILE_ITEM["id"]]),
        patch("OneDriveFUSE.pyfuse3.invalidate_inode",
              side_effect=Exception("FS is unmounting")),
    ):
        trio.run(fuse._poll_once)  # must not raise

    # Cache should still be evicted even if kernel invalidation failed
    assert cache.get(FILE_ITEM["id"], FILE_ITEM["eTag"]) is None


# ---------------------------------------------------------------------------
# _poll_once — runs sync_metadata in a thread
# ---------------------------------------------------------------------------

def test_poll_once_runs_sync_metadata_in_thread(fuse):
    """
    sync_metadata is blocking I/O. Verify it is dispatched via
    trio.to_thread.run_sync so the event loop is not blocked.
    """
    calls = []

    async def fake_run_sync(fn, *args, **kwargs):
        calls.append(fn)
        return fn()  # call it to satisfy the rest of the code

    with (
        patch("OneDriveFUSE.sync_metadata", return_value=[]),
        patch("OneDriveFUSE.trio.to_thread.run_sync", side_effect=fake_run_sync),
    ):
        trio.run(fuse._poll_once)

    assert len(calls) == 1


# ---------------------------------------------------------------------------
# metadata_poller — loop behaviour
# ---------------------------------------------------------------------------

def test_poller_sleeps_between_polls(fuse):
    """Poller must sleep before each poll, not after."""
    poll_count = 0

    async def mock_poll_once():
        nonlocal poll_count
        poll_count += 1

    fuse._poll_once = mock_poll_once

    async def run():
        with trio.move_on_after(0.05):
            await fuse.metadata_poller(interval=0.02)

    trio.run(run)
    assert poll_count == 2


def test_poller_continues_after_poll_error(fuse):
    """A failing poll must not stop the loop."""
    call_count = 0

    async def flaky_poll():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("simulated network failure")

    fuse._poll_once = flaky_poll

    async def run():
        with trio.move_on_after(0.05):
            await fuse.metadata_poller(interval=0.02)

    trio.run(run)
    assert call_count >= 2


def test_poller_uses_default_interval(fuse):
    """Default interval is 300s — verify it is passed to trio.sleep."""
    sleep_calls = []

    async def mock_sleep(seconds):
        sleep_calls.append(seconds)
        # Yield to trio so move_on_after can observe elapsed time and cancel.
        # Cannot use trio.sleep(0) here — the patch affects the whole trio module.
        await trio.lowlevel.checkpoint()

    async def run():
        with trio.move_on_after(0.05):
            with (
                patch("OneDriveFUSE.trio.sleep", side_effect=mock_sleep),
                patch.object(fuse, "_poll_once", new_callable=AsyncMock),
            ):
                await fuse.metadata_poller()

    trio.run(run)
    assert len(sleep_calls) >= 1
    assert sleep_calls[0] == 30
