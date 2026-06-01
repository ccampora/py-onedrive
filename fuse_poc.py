#!/usr/bin/env python3
"""
POC: OneDrive Files On-Demand via FUSE

Files appear in the virtual filesystem immediately (metadata only).
Content is read from /tmp only when the file is actually opened.

Usage:
    python fuse_poc.py [mountpoint]       # default: ~/onedrive-poc
    ls ~/onedrive-poc                     # files appear instantly
    cat ~/onedrive-poc/notes.txt          # content fetched on demand
    Ctrl+C to unmount
"""

import errno
import os
import stat
import time

import pyfuse3
import trio


# Simulates the OneDrive item list + backing content in /tmp.
# In the real implementation this would be built from ~/.py-onedrive/db/
# and content would be fetched from the Graph API on read().
VIRTUAL_FILES = {
    "notes.txt":  "/tmp/notes.txt",
    "report.pdf": "/tmp/report.pdf",
}

ROOT_INODE = pyfuse3.ROOT_INODE


class OneDriveFUSE(pyfuse3.Operations):

    def __init__(self, virtual_files: dict):
        super().__init__()
        self._files = virtual_files
        self._inode_to_name = {i: name for i, name in enumerate(virtual_files, start=2)}
        self._name_to_inode = {name: i for i, name in self._inode_to_name.items()}

    async def getattr(self, inode, ctx=None):
        attrs = pyfuse3.EntryAttributes()
        attrs.st_ino = inode
        attrs.st_nlink = 1
        attrs.st_uid = os.getuid()
        attrs.st_gid = os.getgid()
        attrs.attr_timeout = 1.0
        attrs.entry_timeout = 1.0

        now_ns = int(time.time() * 1e9)
        attrs.st_atime_ns = now_ns
        attrs.st_mtime_ns = now_ns
        attrs.st_ctime_ns = now_ns

        if inode == ROOT_INODE:
            attrs.st_mode = stat.S_IFDIR | 0o755
            attrs.st_size = 0
        elif inode in self._inode_to_name:
            name = self._inode_to_name[inode]
            path = self._files[name]
            attrs.st_mode = stat.S_IFREG | 0o644
            try:
                st = os.stat(path)
                attrs.st_size = st.st_size
                attrs.st_mtime_ns = int(st.st_mtime * 1e9)
            except FileNotFoundError:
                attrs.st_size = 0  # not yet in /tmp — show as empty placeholder
        else:
            raise pyfuse3.FUSEError(errno.ENOENT)

        return attrs

    async def lookup(self, parent_inode, name, ctx=None):
        name = name.decode()
        if parent_inode != ROOT_INODE or name not in self._name_to_inode:
            raise pyfuse3.FUSEError(errno.ENOENT)
        return await self.getattr(self._name_to_inode[name])

    async def opendir(self, inode, ctx):
        if inode != ROOT_INODE:
            raise pyfuse3.FUSEError(errno.ENOTDIR)
        return inode

    async def readdir(self, fh, start_id, token):
        entries = (
            [(b'.', ROOT_INODE), (b'..', ROOT_INODE)]
            + [(name.encode(), inode) for name, inode in self._name_to_inode.items()]
        )
        for idx, (name, inode) in enumerate(entries):
            if idx < start_id:
                continue
            attrs = await self.getattr(inode)
            if not pyfuse3.readdir_reply(token, name, attrs, idx + 1):
                return

    async def open(self, inode, flags, ctx):
        if inode not in self._inode_to_name:
            raise pyfuse3.FUSEError(errno.ENOENT)
        return pyfuse3.FileInfo(fh=inode)

    async def read(self, fh, offset, length):
        if fh not in self._inode_to_name:
            raise pyfuse3.FUSEError(errno.EBADF)
        name = self._inode_to_name[fh]
        path = self._files[name]
        # In the real implementation: fetch from Graph API here instead of /tmp
        print(f"  [on-demand] '{name}' requested — reading from {path}")
        try:
            with open(path, 'rb') as f:
                f.seek(offset)
                return f.read(length)
        except FileNotFoundError:
            raise pyfuse3.FUSEError(errno.ENOENT)


def _create_sample_tmp_files():
    samples = {
        "/tmp/notes.txt":  "Hello from notes.txt!\nThis content was fetched on demand.\n",
        "/tmp/report.pdf": "Fake PDF content for report.pdf.\n",
    }
    for path, content in samples.items():
        if not os.path.exists(path):
            with open(path, 'w') as f:
                f.write(content)
            print(f"Created sample file: {path}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="OneDrive Files On-Demand POC")
    parser.add_argument(
        "mountpoint",
        nargs="?",
        default=os.path.expanduser("~/onedrive-poc"),
        help="Mount point (default: ~/onedrive-poc)",
    )
    args = parser.parse_args()

    mountpoint = args.mountpoint
    os.makedirs(mountpoint, exist_ok=True)

    _create_sample_tmp_files()

    fs = OneDriveFUSE(VIRTUAL_FILES)
    options = set(pyfuse3.default_options)
    options.add('fsname=onedrive-poc')
    pyfuse3.init(fs, mountpoint, options)

    print(f"\nMounted at: {mountpoint}")
    print(f"Files    : {', '.join(VIRTUAL_FILES)}")
    print(f"\nTry:")
    print(f"  ls {mountpoint}")
    print(f"  cat {mountpoint}/notes.txt")
    print(f"\nCtrl+C to unmount.\n")

    try:
        trio.run(pyfuse3.main)
    except KeyboardInterrupt:
        pass
    finally:
        pyfuse3.close(unmount=True)
        print(f"\nUnmounted {mountpoint}")


if __name__ == "__main__":
    main()
