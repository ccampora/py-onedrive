#!/usr/bin/env python3
"""
OneDrive Files On-Demand via FUSE.

Mounts OneDrive as a virtual filesystem where file content is fetched
transparently from the Graph API only when a file is first opened.

Usage:
    python mount.py [--mountpoint ~/onedrive] [--poll-interval 300] [--debug]

Unmount:
    fusermount3 -u ~/onedrive
    # or simply Ctrl+C if running in the foreground
"""

import argparse
import logging
import os
import sys

import pyfuse3
import trio

from Authentication import authenticate
from CacheManager import CacheManager
from Config import create_pyonedrive_config_folder, init_onedrive_database
from Globals import CACHE_FOLDER, LOGGER as logger
from MetadataIndex import MetadataIndex
from OneDriveFUSE import OneDriveFUSE
from Operations import sync_metadata


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Mount OneDrive as a Files On-Demand FUSE filesystem",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--mountpoint",
        default=os.path.expanduser("~/Onedrive"),
        metavar="DIR",
        help="Directory to mount OneDrive",
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=300,
        metavar="SECONDS",
        help="How often (in seconds) to sync metadata changes from OneDrive",
    )
    parser.add_argument(
        "--max-downloads",
        type=int,
        default=4,
        metavar="N",
        help="Maximum simultaneous file downloads (default: 4)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable verbose debug logging and FUSE debug mode",
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging(debug=False):
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # basicConfig is a no-op when handlers are already configured (e.g. under
    # pytest). Set the level explicitly so the caller's intent is always applied.
    logging.getLogger().setLevel(level)


# ---------------------------------------------------------------------------
# FUSE options
# ---------------------------------------------------------------------------

def build_fuse_options(debug=False):
    options = set(pyfuse3.default_options)
    options.add("fsname=onedrive")
    # Detach the mount automatically when the process exits for any reason.
    # Without this, a crashed FUSE process leaves a zombie mount that causes
    # any access to the mountpoint (including `ls ~`) to hang indefinitely.
    options.add("auto_unmount")
    if debug:
        options.add("debug")
    return options


# ---------------------------------------------------------------------------
# Trio entry point
# ---------------------------------------------------------------------------

async def _run_fuse(fs, poll_interval):
    """
    Run pyfuse3.main and the metadata poller as concurrent trio tasks.
    When either task exits (e.g. on unmount or Ctrl+C), the nursery
    cancels the other.
    """
    async with trio.open_nursery() as nursery:
        nursery.start_soon(pyfuse3.main)
        nursery.start_soon(fs.metadata_poller, poll_interval)
        nursery.start_soon(fs.inode_flusher)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv=None):
    args = parse_args(argv)
    setup_logging(args.debug)

    # 1. Ensure config and DB directories exist
    create_pyonedrive_config_folder()
    init_onedrive_database()

    # 2. Authenticate — refresh stored tokens, or run the full auth flow on
    #    first use (opens a browser window).
    logger.info("Authenticating...")
    try:
        authenticate()
    except Exception as e:
        logger.error(f"Authentication failed: {e}")
        sys.exit(1)

    # 3. Load the metadata index from the on-disk DB (~/.py-onedrive/db/).
    #    On first run the DB is empty; sync_metadata below will populate it.
    logger.info("Loading metadata index...")
    index = MetadataIndex()
    index.load()

    # 4. Incremental metadata sync — fetches only changes since the last run.
    #    On first run this performs a full scan of the OneDrive item tree.
    #    If this fails (e.g. no network yet) we mount with cached data and let
    #    the background poller retry.
    logger.info("Syncing metadata from OneDrive...")
    try:
        changed = sync_metadata(index)
        logger.info(f"Metadata sync complete — {len(changed)} item(s) updated")
    except Exception as e:
        logger.warning(f"Initial metadata sync failed ({e}) — mounting with cached data")

    # 5. Mount the FUSE filesystem.
    mountpoint = args.mountpoint
    os.makedirs(mountpoint, exist_ok=True)

    cache = CacheManager(CACHE_FOLDER)
    fs = OneDriveFUSE(index, cache, max_concurrent_downloads=args.max_downloads)
    fuse_options = build_fuse_options(args.debug)

    pyfuse3.init(fs, mountpoint, fuse_options)
    logger.info(f"Mounted at: {mountpoint}")
    logger.info(
        f"Poll interval: {args.poll_interval}s  |  Max concurrent downloads: {args.max_downloads}"
        f"  |  Ctrl+C or `fusermount3 -u {mountpoint}` to unmount"
    )

    # 6. Run until unmounted or interrupted.
    try:
        trio.run(_run_fuse, fs, args.poll_interval)
    except KeyboardInterrupt:
        pass
    finally:
        index.flush_inode_mapping()
        pyfuse3.close(unmount=True)
        logger.info(f"Unmounted {mountpoint}")


if __name__ == "__main__":
    main()
