import errno
import os
import stat
from unittest.mock import patch, MagicMock

import pytest
import trio
import pyfuse3

from MetadataIndex import MetadataIndex, ROOT_INODE
from CacheManager import CacheManager
from OneDriveFUSE import OneDriveFUSE, _parse_datetime_ns, _item_to_attrs, _root_attrs


def _fake_ctx(pid=1):
    ctx = MagicMock()
    ctx.pid = pid
    return ctx


# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

ROOT_ID = "DRIVE!root"

FOLDER_ITEM = {
    "id": "DRIVE!folder1",
    "name": "Documents",
    "parentReference": {"id": ROOT_ID, "path": "/drive/root:"},
    "folder": {"childCount": 2},
    "size": 0,
    "eTag": '"etag-folder1"',
    "lastModifiedDateTime": "2024-03-10T12:00:00Z",
    "createdDateTime": "2024-01-01T00:00:00Z",
    "fileSystemInfo": {
        "lastModifiedDateTime": "2024-03-10T12:00:00Z",
        "createdDateTime": "2024-01-01T00:00:00Z",
    },
}

FILE_ITEM = {
    "id": "DRIVE!file1",
    "name": "notes.txt",
    "parentReference": {"id": "DRIVE!folder1", "path": "/drive/root:/Documents"},
    "file": {"mimeType": "text/plain"},
    "size": 1024,
    "eTag": '"etag-file1"',
    "lastModifiedDateTime": "2024-06-15T08:30:00Z",
    "createdDateTime": "2024-06-01T00:00:00Z",
    "fileSystemInfo": {
        "lastModifiedDateTime": "2024-06-15T08:30:00Z",
        "createdDateTime": "2024-06-01T00:00:00Z",
    },
}

ROOT_FILE = {
    "id": "DRIVE!rootfile",
    "name": "readme.md",
    "parentReference": {"id": ROOT_ID, "path": "/drive/root:"},
    "file": {"mimeType": "text/markdown"},
    "size": 512,
    "eTag": '"etag-rootfile"',
    "lastModifiedDateTime": "2024-02-20T10:00:00Z",
    "createdDateTime": "2024-02-01T00:00:00Z",
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def index(tmp_path):
    db = tmp_path / "db"
    db.mkdir()
    inode_file = tmp_path / ".py-onedrive-inodes"
    with (
        patch("MetadataIndex.ONEDRIVE_DB_FOLDER", str(db)),
        patch("MetadataIndex.INODE_FILE", str(inode_file)),
    ):
        idx = MetadataIndex()
        idx.upsert(FOLDER_ITEM)
        idx.upsert(FILE_ITEM)
        idx.upsert(ROOT_FILE)
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


def run(coro):
    """Run a coroutine synchronously via trio."""
    return trio.from_thread.run_sync(lambda: None) or trio.run(lambda: coro)


# ---------------------------------------------------------------------------
# _parse_datetime_ns
# ---------------------------------------------------------------------------

def test_parse_datetime_utc_z():
    ns = _parse_datetime_ns("2024-06-15T08:30:00Z")
    assert ns > 0
    # 2024-06-15 08:30:00 UTC → known epoch value
    assert ns == pytest.approx(1718440200 * 1_000_000_000, rel=1e-6)


def test_parse_datetime_offset():
    ns = _parse_datetime_ns("2024-06-15T10:30:00+02:00")
    assert ns == pytest.approx(1718440200 * 1_000_000_000, rel=1e-6)


def test_parse_datetime_none_returns_now():
    before = int(__import__("time").time() * 1e9)
    ns = _parse_datetime_ns(None)
    after = int(__import__("time").time() * 1e9)
    assert before <= ns <= after


def test_parse_datetime_invalid_returns_now():
    before = int(__import__("time").time() * 1e9)
    ns = _parse_datetime_ns("not-a-date")
    after = int(__import__("time").time() * 1e9)
    assert before <= ns <= after


# ---------------------------------------------------------------------------
# _item_to_attrs
# ---------------------------------------------------------------------------

def test_item_to_attrs_file(index):
    inode = index.get_inode(FILE_ITEM["id"])
    attrs = _item_to_attrs(inode, FILE_ITEM)

    assert attrs.st_ino == inode
    assert stat.S_ISREG(attrs.st_mode)
    assert attrs.st_size == 1024
    assert attrs.st_nlink == 1
    assert attrs.st_mode & stat.S_IWUSR  # writable


def test_item_to_attrs_folder(index):
    inode = index.get_inode(FOLDER_ITEM["id"])
    attrs = _item_to_attrs(inode, FOLDER_ITEM)

    assert attrs.st_ino == inode
    assert stat.S_ISDIR(attrs.st_mode)
    assert attrs.st_size == 0
    assert attrs.st_nlink == 2


def test_item_to_attrs_uses_filesystem_info_timestamps():
    item = {**FILE_ITEM, "lastModifiedDateTime": "2020-01-01T00:00:00Z"}
    inode = 5
    attrs = _item_to_attrs(inode, item)
    # fileSystemInfo.lastModifiedDateTime (2024-06-15) takes precedence
    assert attrs.st_mtime_ns == pytest.approx(1718440200 * 1_000_000_000, rel=1e-6)


# ---------------------------------------------------------------------------
# _root_attrs
# ---------------------------------------------------------------------------

def test_root_attrs():
    attrs = _root_attrs()
    assert attrs.st_ino == ROOT_INODE
    assert stat.S_ISDIR(attrs.st_mode)
    assert attrs.st_nlink == 2


# ---------------------------------------------------------------------------
# getattr
# ---------------------------------------------------------------------------

def test_getattr_root(fuse):
    attrs = trio.run(fuse.getattr, ROOT_INODE)
    assert attrs.st_ino == ROOT_INODE
    assert stat.S_ISDIR(attrs.st_mode)


def test_getattr_folder(fuse, index):
    inode = index.get_inode(FOLDER_ITEM["id"])
    attrs = trio.run(fuse.getattr, inode)
    assert attrs.st_ino == inode
    assert stat.S_ISDIR(attrs.st_mode)


def test_getattr_file(fuse, index):
    inode = index.get_inode(FILE_ITEM["id"])
    attrs = trio.run(fuse.getattr, inode)
    assert attrs.st_ino == inode
    assert stat.S_ISREG(attrs.st_mode)
    assert attrs.st_size == FILE_ITEM["size"]


def test_getattr_unknown_inode_raises_enoent(fuse):
    with pytest.raises(pyfuse3.FUSEError) as exc_info:
        trio.run(fuse.getattr, 9999)
    assert exc_info.value.errno == errno.ENOENT


# ---------------------------------------------------------------------------
# lookup
# ---------------------------------------------------------------------------

def test_lookup_from_root(fuse, index):
    attrs = trio.run(fuse.lookup, ROOT_INODE, b"Documents")
    folder_inode = index.get_inode(FOLDER_ITEM["id"])
    assert attrs.st_ino == folder_inode
    assert stat.S_ISDIR(attrs.st_mode)


def test_lookup_from_root_file(fuse, index):
    attrs = trio.run(fuse.lookup, ROOT_INODE, b"readme.md")
    file_inode = index.get_inode(ROOT_FILE["id"])
    assert attrs.st_ino == file_inode
    assert stat.S_ISREG(attrs.st_mode)


def test_lookup_from_subfolder(fuse, index):
    folder_inode = index.get_inode(FOLDER_ITEM["id"])
    attrs = trio.run(fuse.lookup, folder_inode, b"notes.txt")
    file_inode = index.get_inode(FILE_ITEM["id"])
    assert attrs.st_ino == file_inode


def test_lookup_missing_name_raises_enoent(fuse):
    with pytest.raises(pyfuse3.FUSEError) as exc_info:
        trio.run(fuse.lookup, ROOT_INODE, b"ghost.txt")
    assert exc_info.value.errno == errno.ENOENT


def test_lookup_unknown_parent_raises_enoent(fuse):
    with pytest.raises(pyfuse3.FUSEError) as exc_info:
        trio.run(fuse.lookup, 9999, b"anything.txt")
    assert exc_info.value.errno == errno.ENOENT


# ---------------------------------------------------------------------------
# opendir
# ---------------------------------------------------------------------------

def test_opendir_root_returns_root_inode(fuse):
    fh = trio.run(fuse.opendir, ROOT_INODE, None)
    assert fh == ROOT_INODE


def test_opendir_folder_returns_inode(fuse, index):
    inode = index.get_inode(FOLDER_ITEM["id"])
    fh = trio.run(fuse.opendir, inode, None)
    assert fh == inode


def test_opendir_file_raises_enotdir(fuse, index):
    inode = index.get_inode(FILE_ITEM["id"])
    with pytest.raises(pyfuse3.FUSEError) as exc_info:
        trio.run(fuse.opendir, inode, None)
    assert exc_info.value.errno == errno.ENOTDIR


def test_opendir_unknown_inode_raises_enoent(fuse):
    with pytest.raises(pyfuse3.FUSEError) as exc_info:
        trio.run(fuse.opendir, 9999, None)
    assert exc_info.value.errno == errno.ENOENT


# ---------------------------------------------------------------------------
# readdir
# ---------------------------------------------------------------------------

def test_readdir_root_lists_children(fuse, index):
    collected = []

    def fake_readdir_reply(token, name, attrs, next_id):
        collected.append((name.decode(), attrs.st_ino))
        return True  # keep going

    with patch("OneDriveFUSE.pyfuse3.readdir_reply", side_effect=fake_readdir_reply):
        trio.run(fuse.readdir, ROOT_INODE, 0, object())

    names = [n for n, _ in collected]
    assert "Documents" in names
    assert "readme.md" in names
    assert "notes.txt" not in names  # notes.txt is inside Documents, not root


def test_readdir_subfolder_lists_children(fuse, index):
    collected = []

    def fake_readdir_reply(token, name, attrs, next_id):
        collected.append(name.decode())
        return True

    folder_inode = index.get_inode(FOLDER_ITEM["id"])
    with patch("OneDriveFUSE.pyfuse3.readdir_reply", side_effect=fake_readdir_reply):
        trio.run(fuse.readdir, folder_inode, 0, object())

    assert collected == ["notes.txt"]


def test_readdir_respects_start_id(fuse, index):
    """start_id > 0 skips the first N entries (FUSE pagination)."""
    collected = []

    def fake_readdir_reply(token, name, attrs, next_id):
        collected.append(name.decode())
        return True

    with patch("OneDriveFUSE.pyfuse3.readdir_reply", side_effect=fake_readdir_reply):
        trio.run(fuse.readdir, ROOT_INODE, 0, object())
    full_count = len(collected)

    collected.clear()
    with patch("OneDriveFUSE.pyfuse3.readdir_reply", side_effect=fake_readdir_reply):
        trio.run(fuse.readdir, ROOT_INODE, 1, object())

    assert len(collected) == full_count - 1


def test_readdir_stops_when_reply_returns_false(fuse, index):
    """readdir must stop as soon as readdir_reply returns False (buffer full)."""
    collected = []

    def fake_readdir_reply(token, name, attrs, next_id):
        collected.append(name.decode())
        return False  # signal: buffer full after first entry

    with patch("OneDriveFUSE.pyfuse3.readdir_reply", side_effect=fake_readdir_reply):
        trio.run(fuse.readdir, ROOT_INODE, 0, object())

    assert len(collected) == 1


# ---------------------------------------------------------------------------
# releasedir
# ---------------------------------------------------------------------------

def test_releasedir_does_not_raise(fuse):
    trio.run(fuse.releasedir, ROOT_INODE)  # must not raise


# ---------------------------------------------------------------------------
# open
# ---------------------------------------------------------------------------

def test_open_valid_file_returns_file_info(fuse, index):
    inode = index.get_inode(FILE_ITEM["id"])
    with patch("OneDriveFUSE.download_file", return_value=FILE_CONTENT):
        fi = trio.run(fuse.open, inode, os.O_RDONLY, _fake_ctx())
    assert fi.fh == inode


def test_open_write_flag_opens_write_handle(fuse, index):
    inode = index.get_inode(FILE_ITEM["id"])
    with patch("OneDriveFUSE.download_file", return_value=b""):
        fi = trio.run(fuse.open, inode, os.O_WRONLY, _fake_ctx())
    assert fi.fh in fuse._write_handles


def test_open_rdwr_flag_opens_write_handle(fuse, index):
    inode = index.get_inode(FILE_ITEM["id"])
    with patch("OneDriveFUSE.download_file", return_value=b""):
        fi = trio.run(fuse.open, inode, os.O_RDWR, _fake_ctx())
    assert fi.fh in fuse._write_handles


def test_open_unknown_inode_raises_enoent(fuse):
    with pytest.raises(pyfuse3.FUSEError) as exc_info:
        trio.run(fuse.open, 9999, os.O_RDONLY, None)
    assert exc_info.value.errno == errno.ENOENT


def test_open_folder_inode_raises_enoent(fuse, index):
    # Folders have no "file" key — open must reject them
    inode = index.get_inode(FOLDER_ITEM["id"])
    with pytest.raises(pyfuse3.FUSEError) as exc_info:
        trio.run(fuse.open, inode, os.O_RDONLY, None)
    assert exc_info.value.errno == errno.ENOENT


# ---------------------------------------------------------------------------
# read
# ---------------------------------------------------------------------------

FILE_CONTENT = b"Hello from OneDrive! This is the file content."


def test_read_cache_miss_raises_eio(fuse, index):
    """read() on an uncached file raises EIO — open() is responsible for downloading."""
    inode = index.get_inode(FILE_ITEM["id"])
    with pytest.raises(pyfuse3.FUSEError) as exc_info:
        trio.run(fuse.read, inode, 0, 100)
    assert exc_info.value.errno == errno.EIO


def test_read_cache_hit_serves_without_download(fuse, index, cache):
    inode = index.get_inode(FILE_ITEM["id"])
    etag = FILE_ITEM["eTag"]
    cache.put(FILE_ITEM["id"], etag, FILE_CONTENT)

    with patch("OneDriveFUSE.download_file") as mock_dl:
        result = trio.run(fuse.read, inode, 0, len(FILE_CONTENT))

    mock_dl.assert_not_called()
    assert result == FILE_CONTENT


def test_read_respects_offset_and_length(fuse, index, cache):
    """read() slices from the cached file at the given offset and length."""
    inode = index.get_inode(FILE_ITEM["id"])
    content = b"ABCDEFGHIJ"
    cache.put(FILE_ITEM["id"], FILE_ITEM["eTag"], content)
    result = trio.run(fuse.read, inode, 2, 4)
    assert result == b"CDEF"


def test_read_second_call_uses_cache(fuse, index, cache):
    """Two consecutive reads both hit the local cache (no double download)."""
    inode = index.get_inode(FILE_ITEM["id"])
    cache.put(FILE_ITEM["id"], FILE_ITEM["eTag"], FILE_CONTENT)
    with patch("OneDriveFUSE.download_file") as mock_dl:
        trio.run(fuse.read, inode, 0, len(FILE_CONTENT))
        trio.run(fuse.read, inode, 0, len(FILE_CONTENT))
    mock_dl.assert_not_called()


def test_read_unknown_fh_raises_ebadf(fuse):
    with pytest.raises(pyfuse3.FUSEError) as exc_info:
        trio.run(fuse.read, 9999, 0, 100)
    assert exc_info.value.errno == errno.EBADF


def test_open_download_failure_raises_eio(fuse, index):
    """open() raises EIO when the download fails."""
    inode = index.get_inode(FILE_ITEM["id"])
    with patch("OneDriveFUSE.download_file", side_effect=IOError("HTTP 503")):
        with pytest.raises(pyfuse3.FUSEError) as exc_info:
            trio.run(fuse.open, inode, os.O_RDONLY, _fake_ctx())
    assert exc_info.value.errno == errno.EIO


def test_open_404_removes_item_from_index_and_raises_enoent(fuse, index):
    """open() removes the item from the index and raises ENOENT on 404."""
    inode = index.get_inode(FILE_ITEM["id"])
    with patch("OneDriveFUSE.download_file", side_effect=FileNotFoundError("404")):
        with patch.object(pyfuse3, "invalidate_inode"):
            with pytest.raises(pyfuse3.FUSEError) as exc_info:
                trio.run(fuse.open, inode, os.O_RDONLY, _fake_ctx())
    assert exc_info.value.errno == errno.ENOENT
    assert index.get_item(FILE_ITEM["id"]) is None


# ---------------------------------------------------------------------------
# release
# ---------------------------------------------------------------------------

def test_release_does_not_raise(fuse, index):
    inode = index.get_inode(FILE_ITEM["id"])
    trio.run(fuse.release, inode)  # must not raise


# ---------------------------------------------------------------------------
# rename (provisional / not-yet-uploaded items)
#
# Regression coverage for: rename() racing release()'s background upload.
# create() only writes a local temp file and a provisional "__pending__..."
# index entry; the real Graph upload happens later in release(). Renaming
# in between must not hit the Graph API with a synthetic id.
# ---------------------------------------------------------------------------

def test_rename_pending_item_is_local_only_no_graph_call(fuse):
    fi, _ = trio.run(
        fuse.create, ROOT_INODE, b"project.json.tmp-999888777", 0o644,
        os.O_WRONLY, _fake_ctx(),
    )

    with patch("OneDriveFUSE.move_rename_item") as mock_move:
        trio.run(
            fuse.rename, ROOT_INODE, b"project.json.tmp-999888777",
            ROOT_INODE, b"project.json", 0, _fake_ctx(),
        )

    mock_move.assert_not_called()

    item = fuse._index.lookup(ROOT_ID, "project.json")
    assert item is not None
    assert item["id"].startswith("__pending__")
    assert fuse._index.lookup(ROOT_ID, "project.json.tmp-999888777") is None

    handle = fuse._write_handles[fi.fh]
    assert handle.name == "project.json"
    assert handle.parent_id == ROOT_ID


def test_rename_pending_item_preserves_inode(fuse):
    """The kernel already knows the inode returned by create() — renaming
    a pending item must not hand out a different one."""
    fi, attrs = trio.run(
        fuse.create, ROOT_INODE, b"draft.txt.tmp-1", 0o644,
        os.O_WRONLY, _fake_ctx(),
    )
    original_inode = attrs.st_ino

    with patch("OneDriveFUSE.move_rename_item"):
        trio.run(
            fuse.rename, ROOT_INODE, b"draft.txt.tmp-1",
            ROOT_INODE, b"draft.txt", 0, _fake_ctx(),
        )

    item = fuse._index.lookup(ROOT_ID, "draft.txt")
    assert fuse._index.get_inode(item["id"]) == original_inode


def test_rename_pending_item_then_upload_uses_new_name(fuse):
    """After a rename-before-upload, release()'s eventual upload must use
    the new name/parent, not the one captured at create() time."""
    fi, _ = trio.run(
        fuse.create, ROOT_INODE, b"project.json.tmp-999888777", 0o644,
        os.O_WRONLY, _fake_ctx(),
    )
    trio.run(fuse.write, fi.fh, 0, b'{"schema":1}')

    with patch("OneDriveFUSE.move_rename_item"):
        trio.run(
            fuse.rename, ROOT_INODE, b"project.json.tmp-999888777",
            ROOT_INODE, b"project.json", 0, _fake_ctx(),
        )

    uploaded_item = {
        **FILE_ITEM, "id": "DRIVE!newfile", "name": "project.json",
        "parentReference": {"id": ROOT_ID},
    }
    with patch("OneDriveFUSE.upload_new_file", return_value=uploaded_item) as mock_upload:
        trio.run(fuse.release, fi.fh)

    mock_upload.assert_called_once()
    args, _ = mock_upload.call_args
    assert args[1] == "project.json"
    assert fuse._index.lookup(ROOT_ID, "project.json")["id"] == "DRIVE!newfile"
