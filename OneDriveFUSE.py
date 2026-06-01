import errno
import os
import stat
import time
from datetime import datetime, timezone

import pyfuse3
import trio

from MetadataIndex import MetadataIndex, ROOT_INODE
from CacheManager import CacheManager
from Operations import download_file, sync_metadata
from Globals import LOGGER as logger

# Processes that probe file content for thumbnails/indexing but should NOT
# trigger on-demand downloads. Add entries as needed for your desktop env.
_NO_FETCH_PROCS = frozenset({
    "cosmic-files",
    "cosmic-files-ap",
    "nautilus",
    "nemo",
    "thunar",
    "dolphin",
    "pcmanfm",
    "tumbler-1",
    "tumbler",
    "evince-thumbnai",     # Evince PDF thumbnailer (comm truncated to 15 chars)
    "tracker-extract",
    "tracker-extract-3",
    "tracker-miner-f",
    "gvfsd-metadata",
    "gvfs-metadata",
    "baloo_file",          # KDE file indexer
    "baloo_file_extr",
})


def _caller_name(pid):
    """
    Return the executable name of the process that owns thread pid.
    Reads the thread-group leader's comm so that worker threads (e.g.
    tokio-rt-worker) resolve back to the parent process (e.g. cosmic-files).
    """
    try:
        # Find the thread group leader (the actual process PID)
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("Tgid:"):
                    tgid = line.split()[1]
                    break
            else:
                tgid = str(pid)
        with open(f"/proc/{tgid}/comm") as f:
            return f.read().strip()
    except OSError:
        return ""


def _parse_datetime_ns(dt_str):
    """Parse an ISO 8601 datetime string to integer nanoseconds since epoch."""
    if not dt_str:
        return int(time.time() * 1e9)
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1e9)
    except (ValueError, OSError):
        return int(time.time() * 1e9)


def _item_to_attrs(inode, item):
    """Build pyfuse3.EntryAttributes from an OneDrive item dict."""
    attrs = pyfuse3.EntryAttributes()
    attrs.st_ino = inode
    attrs.st_uid = os.getuid()
    attrs.st_gid = os.getgid()
    attrs.attr_timeout = 30.0
    attrs.entry_timeout = 30.0

    is_folder = "folder" in item
    if is_folder:
        attrs.st_mode = stat.S_IFDIR | 0o755
        attrs.st_size = 0
        attrs.st_nlink = 2
    else:
        attrs.st_mode = stat.S_IFREG | 0o444  # read-only: download only
        attrs.st_size = item.get("size", 0)
        attrs.st_nlink = 1

    # Prefer fileSystemInfo timestamps (local time), fall back to top-level fields
    fs_info = item.get("fileSystemInfo", {})
    mtime_ns = _parse_datetime_ns(
        fs_info.get("lastModifiedDateTime") or item.get("lastModifiedDateTime")
    )
    ctime_ns = _parse_datetime_ns(
        fs_info.get("createdDateTime") or item.get("createdDateTime")
    )

    attrs.st_mtime_ns = mtime_ns
    attrs.st_atime_ns = mtime_ns
    attrs.st_ctime_ns = ctime_ns
    attrs.st_birthtime_ns = ctime_ns

    return attrs


def _root_attrs():
    """Return EntryAttributes for the virtual root (not stored in DB)."""
    attrs = pyfuse3.EntryAttributes()
    attrs.st_ino = ROOT_INODE
    attrs.st_uid = os.getuid()
    attrs.st_gid = os.getgid()
    attrs.st_mode = stat.S_IFDIR | 0o755
    attrs.st_size = 0
    attrs.st_nlink = 2
    attrs.attr_timeout = 30.0
    attrs.entry_timeout = 30.0
    now_ns = int(time.time() * 1e9)
    attrs.st_atime_ns = now_ns
    attrs.st_mtime_ns = now_ns
    attrs.st_ctime_ns = now_ns
    attrs.st_birthtime_ns = now_ns
    return attrs


def _read_slice(path, offset, length):
    with open(path, "rb") as f:
        f.seek(offset)
        return f.read(length)


class OneDriveFUSE(pyfuse3.Operations):
    """
    FUSE driver for on-demand OneDrive access.

    Step 4 — directory operations: getattr, lookup, opendir, readdir, releasedir.
    Step 5 — file read: open, read, release (not yet implemented).
    """

    def __init__(self, index: MetadataIndex, cache: CacheManager = None, max_concurrent_downloads: int = 4):
        super().__init__()
        self._index = index
        self._cache = cache or CacheManager()
        self._download_limiter = trio.CapacityLimiter(max_concurrent_downloads)
        # item_id -> trio.Event set when the download completes (or fails)
        self._in_flight: dict[str, trio.Event] = {}

    # ------------------------------------------------------------------
    # Directory operations
    # ------------------------------------------------------------------

    async def getattr(self, inode, ctx=None):
        if inode == ROOT_INODE:
            return _root_attrs()

        item_id = self._index.get_id_for_inode(inode)
        if item_id is None:
            raise pyfuse3.FUSEError(errno.ENOENT)

        item = self._index.get_item(item_id)
        if item is None:
            raise pyfuse3.FUSEError(errno.ENOENT)

        return _item_to_attrs(inode, item)

    async def lookup(self, parent_inode, name, ctx=None):
        name = name.decode()
        parent_id = self._inode_to_item_id(parent_inode)
        if parent_id is None:
            raise pyfuse3.FUSEError(errno.ENOENT)

        item = self._index.lookup(parent_id, name)
        if item is None:
            raise pyfuse3.FUSEError(errno.ENOENT)

        inode = self._index.get_inode(item["id"])
        return _item_to_attrs(inode, item)

    async def opendir(self, inode, ctx):
        if inode == ROOT_INODE:
            return inode

        item_id = self._index.get_id_for_inode(inode)
        if item_id is None:
            raise pyfuse3.FUSEError(errno.ENOENT)

        item = self._index.get_item(item_id)
        if item is None or "folder" not in item:
            raise pyfuse3.FUSEError(errno.ENOTDIR)

        return inode

    async def readdir(self, fh, start_id, token):
        parent_id = self._inode_to_item_id(fh)
        if parent_id is None:
            return

        for idx, item in enumerate(self._index.get_children(parent_id)):
            if idx < start_id:
                continue
            inode = self._index.get_inode(item["id"])
            attrs = _item_to_attrs(inode, item)
            if not pyfuse3.readdir_reply(token, item["name"].encode(), attrs, idx + 1):
                return

    async def releasedir(self, fh):
        pass

    # ------------------------------------------------------------------
    # File operations
    # ------------------------------------------------------------------

    async def open(self, inode, flags, ctx):
        # This is a read-only filesystem — reject any write attempt
        if flags & os.O_ACCMODE != os.O_RDONLY:
            raise pyfuse3.FUSEError(errno.EACCES)

        item_id = self._index.get_id_for_inode(inode)
        if item_id is None:
            raise pyfuse3.FUSEError(errno.ENOENT)

        item = self._index.get_item(item_id)
        if item is None or "file" not in item:
            raise pyfuse3.FUSEError(errno.ENOENT)

        etag = item.get("eTag", "")
        if not self._cache.get(item_id, etag):
            caller = _caller_name(ctx.pid)
            if caller in _NO_FETCH_PROCS:
                logger.debug(f"Blocked probe open by {caller}: {item['name']}")
                raise pyfuse3.FUSEError(errno.ENODATA)
            await self._fetch(item_id, item, etag, inode)
            if not self._cache.get(item_id, etag):
                raise pyfuse3.FUSEError(errno.EIO)

        return pyfuse3.FileInfo(fh=inode)

    async def read(self, fh, offset, length):
        item_id = self._index.get_id_for_inode(fh)
        if item_id is None:
            raise pyfuse3.FUSEError(errno.EBADF)

        item = self._index.get_item(item_id)
        if item is None:
            raise pyfuse3.FUSEError(errno.ENOENT)

        etag = item.get("eTag", "")
        cached_path = self._cache.get(item_id, etag)
        if cached_path is None:
            # open() guarantees the file is cached before any read; if we get
            # here without a cached path something went wrong.
            raise pyfuse3.FUSEError(errno.EIO)

        return await trio.to_thread.run_sync(
            lambda: _read_slice(cached_path, offset, length)
        )

    async def _fetch(self, item_id, item, etag, fh):
        """
        Download item_id from OneDrive, respecting two constraints:
          1. At most `max_concurrent_downloads` downloads run simultaneously.
          2. If the same item_id is already downloading, wait for that download
             to finish rather than starting a duplicate.
        """
        if item_id in self._in_flight:
            logger.debug(f"Waiting for in-flight download: {item['name']}")
            await self._in_flight[item_id].wait()
            return

        event = trio.Event()
        self._in_flight[item_id] = event
        try:
            slots_total = self._download_limiter.total_tokens
            slots_used = slots_total - self._download_limiter.available_tokens
            logger.info(
                f"[on-demand] Fetching '{item['name']}' "
                f"(slot {slots_used + 1}/{slots_total})"
            )
            async with self._download_limiter:
                try:
                    # Download AND cache in the thread — cache.put() writes
                    # potentially large content to disk and must not block
                    # the trio event loop.
                    await trio.to_thread.run_sync(
                        lambda: self._download_and_cache(item_id, etag)
                    )
                except FileNotFoundError:
                    logger.warning(
                        f"Item '{item['name']}' not found on OneDrive — removing from index"
                    )
                    self._index.delete(item_id)
                    try:
                        pyfuse3.invalidate_inode(fh, attr_only=False)
                    except Exception:
                        pass
                    raise pyfuse3.FUSEError(errno.ENOENT)
                except IOError as e:
                    logger.error(f"Download failed for {item_id}: {e}")
                    raise pyfuse3.FUSEError(errno.EIO)
        finally:
            self._in_flight.pop(item_id, None)
            event.set()

    def _download_and_cache(self, item_id, etag):
        """Blocking: fetch from OneDrive and write to local cache. Runs in a thread."""
        content = download_file(item_id)
        self._cache.put(item_id, etag, content)

    async def release(self, fh):
        pass

    # ------------------------------------------------------------------
    # Background metadata poller
    # ------------------------------------------------------------------

    async def inode_flusher(self, interval=30):
        """
        Trio task: periodically flush the inode mapping to disk in a thread.
        Keeps the event loop unblocked — _save_inode_mapping() writes a large
        JSON file and must never run on the event loop directly.
        """
        while True:
            await trio.sleep(interval)
            await trio.to_thread.run_sync(self._index.flush_inode_mapping)

    async def metadata_poller(self, interval=300):
        """
        Trio task: sleep `interval` seconds, sync OneDrive metadata, invalidate
        stale kernel inodes and cache entries. Designed to run alongside
        pyfuse3.main inside a trio nursery.

        A single failed poll is logged and skipped — it does not stop the loop.
        """
        while True:
            await trio.sleep(interval)
            try:
                await self._poll_once()
            except Exception as e:
                logger.error(f"Metadata poller error (will retry in {interval}s): {e}")

    async def _poll_once(self):
        """
        Single poll cycle:
          1. Run sync_metadata in a thread (blocking HTTP — must not block trio).
          2. For every changed or deleted item ID, tell the kernel to drop its
             cached attrs/data and evict the local content cache.

        Note: deleted item IDs are still in the inode mapping after
        index.delete() — intentional, since the kernel may hold open handles.
        """
        changed_ids = await trio.to_thread.run_sync(
            lambda: sync_metadata(self._index)
        )

        if not changed_ids:
            logger.debug("Poller: nothing changed")
            return

        logger.info(f"Poller: {len(changed_ids)} item(s) changed — invalidating")
        for item_id in changed_ids:
            inode = self._index.get_inode(item_id)
            try:
                # attr_only=False also drops the kernel page cache for this inode
                pyfuse3.invalidate_inode(inode, attr_only=False)
            except Exception:
                # FS may be in the process of unmounting, or inode already gone
                pass
            self._cache.invalidate(item_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _inode_to_item_id(self, inode):
        """Resolve an inode to its OneDrive item ID, handling root specially."""
        if inode == ROOT_INODE:
            return self._index.get_root_id()
        return self._index.get_id_for_inode(inode)
