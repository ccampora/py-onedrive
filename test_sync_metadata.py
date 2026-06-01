import pytest
from unittest.mock import patch, MagicMock

from MetadataIndex import MetadataIndex
from Operations import sync_metadata, download_file


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ROOT_ID = "DRIVE!root"

def _item(item_id, name, parent_id, is_folder=False):
    item = {
        "id": item_id,
        "name": name,
        "eTag": f'"etag-{item_id}"',
        "size": 0 if is_folder else 512,
        "lastModifiedDateTime": "2024-01-01T00:00:00Z",
        "parentReference": {
            "id": parent_id,
            "path": "/drive/root:" if parent_id == ROOT_ID else "/drive/root:/folder",
        },
    }
    if is_folder:
        item["folder"] = {"childCount": 0}
    else:
        item["file"] = {"mimeType": "application/octet-stream"}
    return item

def _deleted(item_id):
    return {"id": item_id, "deleted": {}}

def _mock_graph_get(pages):
    """
    Build a side_effect list for graph_get that returns one page per call.
    Each page is a dict with "value" and optionally "@odata.nextLink" /
    "@odata.deltaLink".
    """
    responses = []
    for page in pages:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = page
        responses.append(mock_resp)
    return responses


@pytest.fixture
def index(tmp_path):
    db = tmp_path / "db"
    db.mkdir()
    inode_file = tmp_path / ".py-onedrive-inodes"
    with (
        patch("MetadataIndex.ONEDRIVE_DB_FOLDER", str(db)),
        patch("MetadataIndex.INODE_FILE", str(inode_file)),
    ):
        yield MetadataIndex()


# ---------------------------------------------------------------------------
# Basic sync
# ---------------------------------------------------------------------------

def test_new_items_are_upserted(index):
    folder = _item("DRIVE!f1", "Documents", ROOT_ID, is_folder=True)
    file_ = _item("DRIVE!x1", "notes.txt", "DRIVE!f1")

    pages = [{"value": [folder, file_], "@odata.deltaLink": "delta://v2"}]

    with (
        patch("Operations.graph_get", side_effect=_mock_graph_get(pages)),
        patch("Operations.get_deltalink_from_db", return_value=""),
        patch("Operations.save_deltalink_to_db"),
    ):
        changed = sync_metadata(index)

    assert "DRIVE!f1" in changed
    assert "DRIVE!x1" in changed
    assert index.get_item("DRIVE!f1") is not None
    assert index.get_item("DRIVE!x1") is not None


def test_nothing_to_sync_returns_empty_list(index):
    pages = [{"value": [], "@odata.deltaLink": "delta://v2"}]

    with (
        patch("Operations.graph_get", side_effect=_mock_graph_get(pages)),
        patch("Operations.get_deltalink_from_db", return_value=""),
        patch("Operations.save_deltalink_to_db"),
    ):
        changed = sync_metadata(index)

    assert changed == []


def test_root_item_is_skipped(index):
    root = {"id": "DRIVE!root-item", "name": "root", "parentReference": {}}
    pages = [{"value": [root], "@odata.deltaLink": "delta://v2"}]

    with (
        patch("Operations.graph_get", side_effect=_mock_graph_get(pages)),
        patch("Operations.get_deltalink_from_db", return_value=""),
        patch("Operations.save_deltalink_to_db"),
    ):
        changed = sync_metadata(index)

    assert changed == []
    assert index.get_item("DRIVE!root-item") is None


# ---------------------------------------------------------------------------
# Deletions
# ---------------------------------------------------------------------------

def test_deleted_items_are_removed_from_index(index):
    file_ = _item("DRIVE!x1", "notes.txt", ROOT_ID)
    index.upsert(file_)
    assert index.get_item("DRIVE!x1") is not None

    pages = [{"value": [_deleted("DRIVE!x1")], "@odata.deltaLink": "delta://v2"}]

    with (
        patch("Operations.graph_get", side_effect=_mock_graph_get(pages)),
        patch("Operations.get_deltalink_from_db", return_value=""),
        patch("Operations.save_deltalink_to_db"),
    ):
        changed = sync_metadata(index)

    assert "DRIVE!x1" in changed
    assert index.get_item("DRIVE!x1") is None


# ---------------------------------------------------------------------------
# Delta link expiry (410 resyncRequired)
# ---------------------------------------------------------------------------

def test_410_clears_delta_link_and_resyncs(index):
    """A 410 on the delta URL clears the stale link and triggers a fresh full scan."""
    from GraphAPI import GraphAPIError

    file_ = _item("DRIVE!x1", "notes.txt", ROOT_ID)
    fresh_page = [{"value": [file_], "@odata.deltaLink": "delta://fresh"}]

    call_count = {"n": 0}

    def _side_effect(url):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise GraphAPIError(410, "resyncRequired")
        return _mock_graph_get(fresh_page)[0]

    with (
        patch("Operations.graph_get", side_effect=_side_effect),
        patch("Operations.get_deltalink_from_db", return_value="delta://stale"),
        patch("Operations.save_deltalink_to_db") as mock_save,
    ):
        changed = sync_metadata(index)

    # Should have cleared the stale link then saved the new one
    save_calls = [c.kwargs["deltaToken"] for c in mock_save.call_args_list]
    assert "" in save_calls              # stale link cleared
    assert "delta://fresh" in save_calls  # new link saved
    assert "DRIVE!x1" in changed
    assert index.get_item("DRIVE!x1") is not None


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

def test_pagination_follows_next_link(index):
    file_a = _item("DRIVE!a", "a.txt", ROOT_ID)
    file_b = _item("DRIVE!b", "b.txt", ROOT_ID)

    pages = [
        {"value": [file_a], "@odata.nextLink": "https://graph.microsoft.com/next-page"},
        {"value": [file_b], "@odata.deltaLink": "delta://v2"},
    ]

    with (
        patch("Operations.graph_get", side_effect=_mock_graph_get(pages)),
        patch("Operations.get_deltalink_from_db", return_value=""),
        patch("Operations.save_deltalink_to_db"),
    ):
        changed = sync_metadata(index)

    assert "DRIVE!a" in changed
    assert "DRIVE!b" in changed
    assert index.get_item("DRIVE!a") is not None
    assert index.get_item("DRIVE!b") is not None


# ---------------------------------------------------------------------------
# Delta link
# ---------------------------------------------------------------------------

def test_delta_link_is_saved(index):
    pages = [{"value": [], "@odata.deltaLink": "delta://v3"}]

    with (
        patch("Operations.graph_get", side_effect=_mock_graph_get(pages)),
        patch("Operations.get_deltalink_from_db", return_value=""),
        patch("Operations.save_deltalink_to_db") as mock_save,
    ):
        sync_metadata(index)

    mock_save.assert_called_once_with(deltaToken="delta://v3")


def test_existing_delta_link_is_used(index):
    pages = [{"value": [], "@odata.deltaLink": "delta://v4"}]

    with (
        patch("Operations.graph_get", side_effect=_mock_graph_get(pages)) as mock_graph,
        patch("Operations.get_deltalink_from_db", return_value="delta://v3"),
        patch("Operations.save_deltalink_to_db"),
    ):
        sync_metadata(index)

    called_url = mock_graph.call_args[0][0]
    assert called_url == "delta://v3"


# ---------------------------------------------------------------------------
# download_file
# ---------------------------------------------------------------------------

def test_download_file_returns_bytes():
    mock_resp = MagicMock()
    mock_resp.content = b"file content bytes"

    with patch("Operations.graph_get", return_value=mock_resp):
        result = download_file("DRIVE!x1")

    assert result == b"file content bytes"


def test_download_file_raises_on_graph_error():
    from GraphAPI import GraphAPIError
    with patch("Operations.graph_get", side_effect=GraphAPIError(404, "not found")):
        with pytest.raises(IOError):
            download_file("DRIVE!x1")


def test_download_file_uses_correct_url():
    mock_resp = MagicMock()
    mock_resp.content = b""

    with patch("Operations.graph_get", return_value=mock_resp) as mock_graph:
        download_file("DRIVE!abc123")

    called_url = mock_graph.call_args[0][0]
    assert "DRIVE!abc123" in called_url
    assert "content" in called_url
