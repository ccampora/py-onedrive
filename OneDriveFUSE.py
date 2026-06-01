import dataclasses
import errno
import os
import stat
import tempfile
import time
from datetime import datetime, timezone

import pyfuse3
import trio

from MetadataIndex import MetadataIndex, ROOT_INODE
from CacheManager import CacheManager
from Operations import (
    download_file, sync_metadata,
    upload_new_file, overwrite_file,
    create_folder, delete_remote_item, move_rename_item,
)
from Globals import LOGGER as logger


@dataclasses.dataclass
class _WriteHandle:
    """Tracks an open file handle that was opened for writing."""
    tmp_path: str           # temp file buffering writes before upload
    item_id: str | None     # None for newly created files
    parent_id: str
    parent_inode: int       # used to invalidate the dir listing after upload
    name: str
    inode: int              # provisional or real inode
    size: int = 0           # current byte count (for getattr during write)

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

    is_folder = "folder" in item
    if is_folder:
        # Directories: never cache so ls always reflects the real index.
        attrs.attr_timeout = 0.0
        attrs.entry_timeout = 0.0
        attrs.st_mode = stat.S_IFDIR | 0o755
        attrs.st_size = 0
        attrs.st_nlink = 2
    else:
        attrs.attr_timeout = 30.0
        attrs.entry_timeout = 30.0
        attrs.st_mode = stat.S_IFREG | 0o644
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
    attrs.attr_timeout = 0.0
    attrs.entry_timeout = 0.0
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


def _write_slice(path, offset, buf):
    with open(path, "r+b") as f:
        f.seek(offset)
        f.write(buf)


def _truncate(path, size):
    with open(path, "r+b") as f:
        f.truncate(size)


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
        # Write handles: fh -> _WriteHandle (fh values start at 2^31 to avoid
        # colliding with read fhs, which reuse the inode number)
        self._write_handles: dict[int, _WriteHandle] = {}
        self._next_write_fh: int = 1 << 31
        # Provisional attrs for files that are being created but not yet uploaded
        self._pending_attrs: dict[int, pyfuse3.EntryAttributes] = {}

    # ------------------------------------------------------------------
    # Directory operations
    # ------------------------------------------------------------------

    async def getattr(self, inode, ctx=None):
        if inode == ROOT_INODE:
            return _root_attrs()

        # Provisional attrs for files being created but not yet uploaded
        if inode in self._pending_attrs:
            return self._pending_attrs[inode]

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
        item_id = self._index.get_id_for_inode(inode)
        if item_id is None:
            raise pyfuse3.FUSEError(errno.ENOENT)

        item = self._index.get_item(item_id)
        if item is None or "file" not in item:
            raise pyfuse3.FUSEError(errno.ENOENT)

        access = flags & os.O_ACCMODE
        is_write = access in (os.O_WRONLY, os.O_RDWR)

        if is_write:
            parent_id = item.get("parentReference", {}).get("id", "")
            parent_inode = ROOT_INODE if parent_id == self._index.get_root_id() else self._index.get_inode(parent_id)
            return await self._open_for_write(inode, item_id, item, flags, parent_inode)

        # Read-only path: ensure file is cached
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

    async def _open_for_write(self, inode, item_id, item, flags, parent_inode):
        """Set up a write handle for an existing file opened with write flags."""
        tmp = tempfile.NamedTemporaryFile(delete=False, prefix="onedrive-write-")
        tmp_path = tmp.name

        truncate = bool(flags & os.O_TRUNC)
        if not truncate:
            # Pre-populate temp file with current content so O_RDWR reads work
            etag = item.get("eTag", "")
            cached = self._cache.get(item_id, etag)
            if not cached:
                await self._fetch(item_id, item, etag, inode)
                cached = self._cache.get(item_id, etag)
            if cached:
                with open(cached, "rb") as src:
                    tmp.write(src.read())

        tmp.close()
        size = os.path.getsize(tmp_path)
        fh = self._next_write_fh
        self._next_write_fh += 1
        parent_id = item.get("parentReference", {}).get("id", "")
        self._write_handles[fh] = _WriteHandle(
            tmp_path=tmp_path, item_id=item_id,
            parent_id=parent_id, parent_inode=parent_inode,
            name=item["name"], inode=inode, size=size,
        )
        logger.debug(f"Opened '{item['name']}' for writing (fh={fh}, truncate={truncate})")
        return pyfuse3.FileInfo(fh=fh)

    async def read(self, fh, offset, length):
        # Write handle: read from the temp file
        if fh in self._write_handles:
            path = self._write_handles[fh].tmp_path
            return await trio.to_thread.run_sync(
                lambda: _read_slice(path, offset, length)
            )

        item_id = self._index.get_id_for_inode(fh)
        if item_id is None:
            raise pyfuse3.FUSEError(errno.EBADF)

        item = self._index.get_item(item_id)
        if item is None:
            raise pyfuse3.FUSEError(errno.ENOENT)

        etag = item.get("eTag", "")
        cached_path = self._cache.get(item_id, etag)
        if cached_path is None:
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

    async def write(self, fh, offset, buf):
        handle = self._write_handles.get(fh)
        if handle is None:
            raise pyfuse3.FUSEError(errno.EBADF)
        await trio.to_thread.run_sync(
            lambda: _write_slice(handle.tmp_path, offset, buf)
        )
        end = offset + len(buf)
        if end > handle.size:
            handle.size = end
        # Keep pending attrs in sync so getattr returns the right size
        if handle.inode in self._pending_attrs:
            self._pending_attrs[handle.inode].st_size = handle.size
        return len(buf)

    async def release(self, fh):
        handle = self._write_handles.pop(fh, None)
        if handle is None:
            return  # read-only handle — nothing to do

        logger.info(f"[upload] '{handle.name}' ({handle.size} bytes)")
        try:
            await trio.to_thread.run_sync(lambda: self._upload(handle))
            # Force the kernel to discard its cached directory listing so that
            # the new/updated file shows up immediately in ls and the file manager.
            try:
                pyfuse3.invalidate_inode(handle.parent_inode, attr_only=False)
            except Exception:
                pass
        except Exception as e:
            logger.error(f"[upload] failed for '{handle.name}': {e}")
        finally:
            self._pending_attrs.pop(handle.inode, None)
            try:
                os.unlink(handle.tmp_path)
            except OSError:
                pass

    def _upload(self, handle: _WriteHandle):
        """Blocking: read temp file and upload to OneDrive. Runs in a thread."""
        with open(handle.tmp_path, "rb") as f:
            content = f.read()
        if handle.item_id:
            item = overwrite_file(handle.item_id, content)
        else:
            item = upload_new_file(handle.parent_id, handle.name, content)
            # Remove the provisional placeholder so the real item takes its place
            self._index.delete(f"__pending__{handle.name}__{handle.parent_id}")
        self._index.upsert(item)
        # Update the content cache so subsequent reads are local
        etag = item.get("eTag", "")
        self._cache.put(item["id"], etag, content)
        logger.info(f"[upload] '{handle.name}' complete — id={item['id']}")

    # ------------------------------------------------------------------
    # Write: create, mkdir, unlink, rmdir, rename, setattr
    # ------------------------------------------------------------------

    async def create(self, parent_inode, name, mode, flags, ctx):
        name = name.decode()
        parent_id = self._inode_to_item_id(parent_inode)
        if parent_id is None:
            raise pyfuse3.FUSEError(errno.ENOENT)

        tmp = tempfile.NamedTemporaryFile(delete=False, prefix="onedrive-write-")
        tmp.close()

        inode = self._index.get_inode(f"__pending__{name}__{parent_id}")
        now_ns = int(time.time() * 1e9)
        attrs = pyfuse3.EntryAttributes()
        attrs.st_ino = inode
        attrs.st_uid = os.getuid()
        attrs.st_gid = os.getgid()
        attrs.st_mode = stat.S_IFREG | 0o644
        attrs.st_size = 0
        attrs.st_nlink = 1
        attrs.attr_timeout = 1.0
        attrs.entry_timeout = 1.0
        attrs.st_atime_ns = now_ns
        attrs.st_mtime_ns = now_ns
        attrs.st_ctime_ns = now_ns
        attrs.st_birthtime_ns = now_ns
        self._pending_attrs[inode] = attrs

        fh = self._next_write_fh
        self._next_write_fh += 1
        self._write_handles[fh] = _WriteHandle(
            tmp_path=tmp.name, item_id=None,
            parent_id=parent_id, parent_inode=parent_inode,
            name=name, inode=inode, size=0,
        )
        logger.debug(f"create '{name}' in parent {parent_id} (fh={fh})")
        return pyfuse3.FileInfo(fh=fh), attrs

    async def mkdir(self, parent_inode, name, mode, ctx):
        name = name.decode()
        parent_id = self._inode_to_item_id(parent_inode)
        if parent_id is None:
            raise pyfuse3.FUSEError(errno.ENOENT)

        try:
            item = await trio.to_thread.run_sync(
                lambda: create_folder(parent_id, name)
            )
        except FileExistsError:
            raise pyfuse3.FUSEError(errno.EEXIST)
        except IOError as e:
            logger.error(f"mkdir '{name}': {e}")
            raise pyfuse3.FUSEError(errno.EIO)

        self._index.upsert(item)
        inode = self._index.get_inode(item["id"])
        try:
            pyfuse3.invalidate_inode(parent_inode, attr_only=False)
        except Exception:
            pass
        logger.info(f"mkdir '{name}' → {item['id']}")
        return _item_to_attrs(inode, item)

    async def unlink(self, parent_inode, name, ctx):
        name = name.decode()
        parent_id = self._inode_to_item_id(parent_inode)
        if parent_id is None:
            raise pyfuse3.FUSEError(errno.ENOENT)

        item = self._index.lookup(parent_id, name)
        if item is None:
            raise pyfuse3.FUSEError(errno.ENOENT)

        item_id = item["id"]
        try:
            await trio.to_thread.run_sync(lambda: delete_remote_item(item_id))
        except IOError as e:
            logger.error(f"unlink '{name}': {e}")
            raise pyfuse3.FUSEError(errno.EIO)

        inode = self._index.get_inode(item_id)
        self._index.delete(item_id)
        self._cache.invalidate(item_id)
        for inv_inode in (inode, parent_inode):
            try:
                pyfuse3.invalidate_inode(inv_inode, attr_only=False)
            except Exception:
                pass
        logger.info(f"unlink '{name}' ({item_id})")

    async def rmdir(self, parent_inode, name, ctx):
        # OneDrive deletes non-empty folders too, but we mirror POSIX: only
        # delete if the folder is empty in our index.
        name = name.decode()
        parent_id = self._inode_to_item_id(parent_inode)
        if parent_id is None:
            raise pyfuse3.FUSEError(errno.ENOENT)

        item = self._index.lookup(parent_id, name)
        if item is None:
            raise pyfuse3.FUSEError(errno.ENOENT)
        if "folder" not in item:
            raise pyfuse3.FUSEError(errno.ENOTDIR)

        item_id = item["id"]
        if self._index.get_children(item_id):
            raise pyfuse3.FUSEError(errno.ENOTEMPTY)

        try:
            await trio.to_thread.run_sync(lambda: delete_remote_item(item_id))
        except IOError as e:
            logger.error(f"rmdir '{name}': {e}")
            raise pyfuse3.FUSEError(errno.EIO)

        inode = self._index.get_inode(item_id)
        self._index.delete(item_id)
        for inv_inode in (inode, parent_inode):
            try:
                pyfuse3.invalidate_inode(inv_inode, attr_only=False)
            except Exception:
                pass
        logger.info(f"rmdir '{name}' ({item_id})")

    async def rename(self, parent_inode_old, name_old, parent_inode_new, name_new, flags, ctx):
        name_old = name_old.decode()
        name_new = name_new.decode()

        parent_id_old = self._inode_to_item_id(parent_inode_old)
        parent_id_new = self._inode_to_item_id(parent_inode_new)
        if parent_id_old is None or parent_id_new is None:
            raise pyfuse3.FUSEError(errno.ENOENT)

        item = self._index.lookup(parent_id_old, name_old)
        if item is None:
            raise pyfuse3.FUSEError(errno.ENOENT)

        item_id = item["id"]
        new_name = name_new if name_new != name_old else None
        new_parent = parent_id_new if parent_id_new != parent_id_old else None

        try:
            updated = await trio.to_thread.run_sync(
                lambda: move_rename_item(item_id, new_name=new_name, new_parent_id=new_parent)
            )
        except IOError as e:
            logger.error(f"rename '{name_old}' → '{name_new}': {e}")
            raise pyfuse3.FUSEError(errno.EIO)

        self._index.upsert(updated)
        for inv_inode in {parent_inode_old, parent_inode_new}:
            try:
                pyfuse3.invalidate_inode(inv_inode, attr_only=False)
            except Exception:
                pass
        logger.info(f"rename '{name_old}' → '{name_new}' (parent changed: {new_parent is not None})")

    async def setattr(self, inode, attr, fields, fh, ctx):
        # Find a write handle for this inode (fh may be a write fh or the inode itself)
        handle = self._write_handles.get(fh)

        if fields.update_size and handle is not None:
            # Truncate the temp file
            new_size = attr.st_size
            await trio.to_thread.run_sync(
                lambda: _truncate(handle.tmp_path, new_size)
            )
            handle.size = new_size
            if inode in self._pending_attrs:
                self._pending_attrs[inode].st_size = new_size

        # Return current attrs
        return await self.getattr(inode, ctx)

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
