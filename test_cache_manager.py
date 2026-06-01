import os
import pytest

from CacheManager import CacheManager


@pytest.fixture
def cache(tmp_path):
    return CacheManager(cache_dir=str(tmp_path / "cache"))


# ---------------------------------------------------------------------------
# get — miss cases
# ---------------------------------------------------------------------------

def test_get_miss_no_files(cache):
    assert cache.get("ITEM1", '"etag-1"') is None


def test_get_miss_content_exists_but_no_etag(cache, tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(exist_ok=True)
    (cache_dir / "ITEM1").write_bytes(b"data")
    assert cache.get("ITEM1", '"etag-1"') is None


def test_get_miss_stale_etag(cache):
    cache.put("ITEM1", '"etag-v1"', b"original content")
    assert cache.get("ITEM1", '"etag-v2"') is None


# ---------------------------------------------------------------------------
# get — hit cases
# ---------------------------------------------------------------------------

def test_get_hit_returns_path(cache):
    cache.put("ITEM1", '"etag-1"', b"hello")
    path = cache.get("ITEM1", '"etag-1"')
    assert path is not None
    assert os.path.exists(path)


def test_get_hit_content_is_correct(cache):
    content = b"file content bytes"
    cache.put("ITEM1", '"etag-1"', content)
    path = cache.get("ITEM1", '"etag-1"')
    with open(path, "rb") as f:
        assert f.read() == content


# ---------------------------------------------------------------------------
# put
# ---------------------------------------------------------------------------

def test_put_writes_content_atomically(cache, tmp_path):
    cache.put("ITEM2", '"etag-2"', b"data")
    cache_dir = tmp_path / "cache"
    assert (cache_dir / "ITEM2").exists()
    assert (cache_dir / "ITEM2.etag").exists()
    assert not (cache_dir / "ITEM2.tmp").exists()


def test_put_overwrites_stale_entry(cache):
    cache.put("ITEM3", '"etag-v1"', b"old")
    cache.put("ITEM3", '"etag-v2"', b"new")
    path = cache.get("ITEM3", '"etag-v2"')
    with open(path, "rb") as f:
        assert f.read() == b"new"


def test_put_returns_content_path(cache):
    path = cache.put("ITEM4", '"etag-4"', b"x")
    assert os.path.exists(path)


# ---------------------------------------------------------------------------
# invalidate
# ---------------------------------------------------------------------------

def test_invalidate_removes_cached_file(cache):
    cache.put("ITEM5", '"etag-5"', b"data")
    assert cache.get("ITEM5", '"etag-5"') is not None

    cache.invalidate("ITEM5")
    assert cache.get("ITEM5", '"etag-5"') is None


def test_invalidate_nonexistent_does_not_raise(cache):
    cache.invalidate("GHOST")  # must not raise


def test_invalidate_removes_both_content_and_etag_files(cache, tmp_path):
    cache.put("ITEM6", '"etag-6"', b"data")
    cache.invalidate("ITEM6")
    cache_dir = tmp_path / "cache"
    assert not (cache_dir / "ITEM6").exists()
    assert not (cache_dir / "ITEM6.etag").exists()
