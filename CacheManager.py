import os

from Globals import CACHE_FOLDER, LOGGER as logger


class CacheManager:
    """
    Local on-disk cache for OneDrive file content.

    Files are stored at <cache_dir>/<item_id>.
    The eTag at the time of download is stored alongside at <item_id>.etag.
    A cached entry is valid only when its stored eTag matches the eTag
    currently in the MetadataIndex — if OneDrive reports a different eTag
    the entry is treated as a miss and re-downloaded.
    """

    def __init__(self, cache_dir=CACHE_FOLDER):
        self._dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)

    def get(self, item_id, current_etag):
        """
        Return the path to the cached content if it exists and is fresh.
        Returns None on a cache miss or when the eTag has changed.
        """
        content_path = self._content_path(item_id)
        etag_path = self._etag_path(item_id)

        if not os.path.exists(content_path) or not os.path.exists(etag_path):
            return None

        try:
            with open(etag_path, "r") as f:
                cached_etag = f.read().strip()
        except OSError:
            return None

        if cached_etag != current_etag:
            logger.debug(f"Cache stale for {item_id} (etag mismatch)")
            return None

        return content_path

    def put(self, item_id, etag, content):
        """
        Write content to the cache atomically.
        Returns the path to the cached file.
        """
        content_path = self._content_path(item_id)
        etag_path = self._etag_path(item_id)

        tmp = content_path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(content)
        os.replace(tmp, content_path)

        with open(etag_path, "w") as f:
            f.write(etag)

        logger.debug(f"Cached {item_id} ({len(content)} bytes)")
        return content_path

    def invalidate(self, item_id):
        """Remove cached content and eTag for item_id."""
        for path in (self._content_path(item_id), self._etag_path(item_id)):
            try:
                os.remove(path)
            except FileNotFoundError:
                pass

    def _content_path(self, item_id):
        return os.path.join(self._dir, item_id)

    def _etag_path(self, item_id):
        return os.path.join(self._dir, item_id + ".etag")
