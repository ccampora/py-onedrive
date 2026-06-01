import json
import os
import pytest
from unittest.mock import patch

from MetadataIndex import MetadataIndex, ROOT_INODE


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
    "eTag": '"etag-folder1"',
    "lastModifiedDateTime": "2024-01-01T00:00:00Z",
}

FILE_ITEM = {
    "id": "DRIVE!file1",
    "name": "notes.txt",
    "parentReference": {"id": "DRIVE!folder1", "path": "/drive/root:/Documents"},
    "file": {"mimeType": "text/plain"},
    "size": 1234,
    "eTag": '"etag-file1"',
    "lastModifiedDateTime": "2024-01-02T00:00:00Z",
}

ROOT_LEVEL_FILE = {
    "id": "DRIVE!rootfile",
    "name": "readme.md",
    "parentReference": {"id": ROOT_ID, "path": "/drive/root:"},
    "file": {"mimeType": "text/markdown"},
    "size": 42,
    "eTag": '"etag-rootfile"',
    "lastModifiedDateTime": "2024-01-03T00:00:00Z",
}


@pytest.fixture
def index(tmp_path):
    """MetadataIndex with DB and inode file redirected to a temp directory."""
    db_folder = tmp_path / "db"
    db_folder.mkdir()
    inode_file = tmp_path / ".py-onedrive-inodes"

    with (
        patch("MetadataIndex.ONEDRIVE_DB_FOLDER", str(db_folder)),
        patch("MetadataIndex.INODE_FILE", str(inode_file)),
    ):
        idx = MetadataIndex()
        yield idx, tmp_path


# ---------------------------------------------------------------------------
# load()
# ---------------------------------------------------------------------------

def test_load_empty_db(index):
    idx, tmp = index
    idx.load()
    assert idx.get_item("anything") is None


def test_load_populates_index(index):
    idx, tmp = index
    db = tmp / "db"
    (db / FOLDER_ITEM["id"]).write_text(json.dumps(FOLDER_ITEM))
    (db / FILE_ITEM["id"]).write_text(json.dumps(FILE_ITEM))
    (db / ROOT_LEVEL_FILE["id"]).write_text(json.dumps(ROOT_LEVEL_FILE))

    idx.load()

    assert idx.get_item(FOLDER_ITEM["id"]) is not None
    assert idx.get_item(FILE_ITEM["id"]) is not None


def test_load_discovers_root(index):
    idx, tmp = index
    db = tmp / "db"
    (db / FOLDER_ITEM["id"]).write_text(json.dumps(FOLDER_ITEM))

    idx.load()

    assert idx.get_root_id() == ROOT_ID
    assert idx.get_inode(ROOT_ID) == ROOT_INODE


def test_load_skips_corrupted_files(index):
    idx, tmp = index
    db = tmp / "db"
    (db / "bad-file").write_text("not json {{")
    (db / FOLDER_ITEM["id"]).write_text(json.dumps(FOLDER_ITEM))

    idx.load()  # must not raise

    assert idx.get_item(FOLDER_ITEM["id"]) is not None


# ---------------------------------------------------------------------------
# get_children() / lookup()
# ---------------------------------------------------------------------------

def test_get_children_of_root(index):
    idx, tmp = index
    idx.upsert(FOLDER_ITEM)
    idx.upsert(ROOT_LEVEL_FILE)
    idx._root_id = ROOT_ID

    children = idx.get_children(ROOT_ID)
    child_ids = {c["id"] for c in children}

    assert FOLDER_ITEM["id"] in child_ids
    assert ROOT_LEVEL_FILE["id"] in child_ids
    assert FILE_ITEM["id"] not in child_ids


def test_get_children_of_folder(index):
    idx, _ = index
    idx.upsert(FOLDER_ITEM)
    idx.upsert(FILE_ITEM)

    children = idx.get_children(FOLDER_ITEM["id"])

    assert len(children) == 1
    assert children[0]["id"] == FILE_ITEM["id"]


def test_lookup_by_parent_and_name(index):
    idx, _ = index
    idx.upsert(FOLDER_ITEM)
    idx.upsert(FILE_ITEM)

    result = idx.lookup(FOLDER_ITEM["id"], "notes.txt")

    assert result is not None
    assert result["id"] == FILE_ITEM["id"]


def test_lookup_missing_returns_none(index):
    idx, _ = index
    idx.upsert(FOLDER_ITEM)

    assert idx.lookup(FOLDER_ITEM["id"], "ghost.txt") is None


# ---------------------------------------------------------------------------
# upsert()
# ---------------------------------------------------------------------------

def test_upsert_writes_db_file(index):
    idx, tmp = index
    idx.upsert(FILE_ITEM)

    db_file = tmp / "db" / FILE_ITEM["id"]
    assert db_file.exists()
    saved = json.loads(db_file.read_text())
    assert saved["name"] == "notes.txt"


def test_upsert_handles_rename(index):
    idx, _ = index
    idx.upsert(FILE_ITEM)

    renamed = {**FILE_ITEM, "name": "renamed.txt"}
    idx.upsert(renamed)

    assert idx.lookup(FOLDER_ITEM["id"], "notes.txt") is None
    assert idx.lookup(FOLDER_ITEM["id"], "renamed.txt") is not None


def test_upsert_handles_move(index):
    idx, _ = index
    idx.upsert(FOLDER_ITEM)
    idx.upsert(FILE_ITEM)

    moved = {**FILE_ITEM, "parentReference": {"id": ROOT_ID, "path": "/drive/root:"}}
    idx.upsert(moved)

    assert idx.lookup(FOLDER_ITEM["id"], "notes.txt") is None
    assert idx.lookup(ROOT_ID, "notes.txt") is not None


# ---------------------------------------------------------------------------
# delete()
# ---------------------------------------------------------------------------

def test_delete_removes_from_index(index):
    idx, _ = index
    idx.upsert(FILE_ITEM)
    idx.delete(FILE_ITEM["id"])

    assert idx.get_item(FILE_ITEM["id"]) is None
    assert idx.lookup(FOLDER_ITEM["id"], "notes.txt") is None


def test_delete_removes_db_file(index):
    idx, tmp = index
    idx.upsert(FILE_ITEM)
    idx.delete(FILE_ITEM["id"])

    assert not (tmp / "db" / FILE_ITEM["id"]).exists()


def test_delete_nonexistent_does_not_raise(index):
    idx, _ = index
    idx.delete("ghost-id")  # must not raise


# ---------------------------------------------------------------------------
# Inode management
# ---------------------------------------------------------------------------

def test_inode_assigned_on_first_upsert(index):
    idx, _ = index
    idx.upsert(FILE_ITEM)
    inode = idx.get_inode(FILE_ITEM["id"])
    assert isinstance(inode, int)
    assert inode >= 2


def test_inode_stable_across_instances(index):
    idx, tmp = index
    idx.upsert(FILE_ITEM)
    first_inode = idx.get_inode(FILE_ITEM["id"])

    # Simulate restart: new instance, same inode file
    with (
        patch("MetadataIndex.ONEDRIVE_DB_FOLDER", str(tmp / "db")),
        patch("MetadataIndex.INODE_FILE", str(tmp / ".py-onedrive-inodes")),
    ):
        idx2 = MetadataIndex()
        idx2.load()
        second_inode = idx2.get_inode(FILE_ITEM["id"])

    assert first_inode == second_inode


def test_root_inode_is_always_one(index):
    idx, tmp = index
    db = tmp / "db"
    (db / FOLDER_ITEM["id"]).write_text(json.dumps(FOLDER_ITEM))
    idx.load()

    assert idx.get_inode(ROOT_ID) == ROOT_INODE


def test_get_id_for_inode_roundtrip(index):
    idx, _ = index
    idx.upsert(FILE_ITEM)
    inode = idx.get_inode(FILE_ITEM["id"])
    assert idx.get_id_for_inode(inode) == FILE_ITEM["id"]


def test_get_id_for_unknown_inode_returns_none(index):
    idx, _ = index
    assert idx.get_id_for_inode(9999) is None
