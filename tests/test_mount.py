import logging
import os
import sys
from unittest.mock import patch, MagicMock, call

import pytest
import pyfuse3

import os
from mount import parse_args, setup_logging, build_fuse_options, main

_DEFAULT_MOUNTPOINT = os.path.expanduser("~/Onedrive")


# ---------------------------------------------------------------------------
# parse_args
# ---------------------------------------------------------------------------

def test_parse_args_defaults():
    args = parse_args([])
    assert args.mountpoint == _DEFAULT_MOUNTPOINT
    assert args.poll_interval == 300
    assert args.debug is False


def test_parse_args_custom_mountpoint():
    args = parse_args(["--mountpoint", "/mnt/cloud"])
    assert args.mountpoint == "/mnt/cloud"


def test_parse_args_custom_poll_interval():
    args = parse_args(["--poll-interval", "60"])
    assert args.poll_interval == 60


def test_parse_args_debug_flag():
    args = parse_args(["--debug"])
    assert args.debug is True


def test_parse_args_all_options():
    args = parse_args([
        "--mountpoint", "/tmp/od",
        "--poll-interval", "120",
        "--debug",
    ])
    assert args.mountpoint == "/tmp/od"
    assert args.poll_interval == 120
    assert args.debug is True


# ---------------------------------------------------------------------------
# setup_logging
# ---------------------------------------------------------------------------

def test_setup_logging_default_is_info():
    setup_logging(debug=False)
    assert logging.getLogger().level == logging.INFO


def test_setup_logging_debug_sets_debug():
    setup_logging(debug=True)
    assert logging.getLogger().level == logging.DEBUG
    # restore to avoid polluting other tests
    logging.getLogger().setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# build_fuse_options
# ---------------------------------------------------------------------------

def test_build_fuse_options_includes_fsname():
    options = build_fuse_options()
    assert "fsname=onedrive" in options


def test_build_fuse_options_debug_adds_debug_flag():
    options = build_fuse_options(debug=True)
    assert "debug" in options


def test_build_fuse_options_no_debug_by_default():
    options = build_fuse_options(debug=False)
    assert "debug" not in options


# ---------------------------------------------------------------------------
# main — startup sequence
# ---------------------------------------------------------------------------

def _patched_main(argv=None, sync_raises=False, auth_raises=False):
    """Run main() with all external calls mocked."""
    mock_index = MagicMock()
    mock_index.load.return_value = None
    mock_fs = MagicMock()

    sync_side_effect = Exception("no network") if sync_raises else None
    auth_side_effect = Exception("auth failed") if auth_raises else None

    with (
        patch("mount.create_pyonedrive_config_folder"),
        patch("mount.init_onedrive_database"),
        patch("mount.authenticate", side_effect=auth_side_effect),
        patch("mount.MetadataIndex", return_value=mock_index),
        patch("mount.sync_metadata",
              return_value=[] if not sync_raises else None,
              side_effect=sync_side_effect),
        patch("mount.CacheManager"),
        patch("mount.OneDriveFUSE", return_value=mock_fs),
        patch("mount.pyfuse3.init"),
        patch("mount.pyfuse3.close"),
        patch("mount.trio.run"),
        patch("mount.os.makedirs"),
    ):
        try:
            main(argv or [])
        except SystemExit as e:
            return e.code
    return 0


def test_main_calls_authenticate():
    with (
        patch("mount.create_pyonedrive_config_folder"),
        patch("mount.init_onedrive_database"),
        patch("mount.authenticate") as mock_auth,
        patch("mount.MetadataIndex"),
        patch("mount.sync_metadata", return_value=[]),
        patch("mount.CacheManager"),
        patch("mount.OneDriveFUSE"),
        patch("mount.pyfuse3.init"),
        patch("mount.pyfuse3.close"),
        patch("mount.trio.run"),
        patch("mount.os.makedirs"),
    ):
        main([])
    mock_auth.assert_called_once()


def test_main_calls_index_load():
    mock_index = MagicMock()
    with (
        patch("mount.create_pyonedrive_config_folder"),
        patch("mount.init_onedrive_database"),
        patch("mount.authenticate"),
        patch("mount.MetadataIndex", return_value=mock_index),
        patch("mount.sync_metadata", return_value=[]),
        patch("mount.CacheManager"),
        patch("mount.OneDriveFUSE"),
        patch("mount.pyfuse3.init"),
        patch("mount.pyfuse3.close"),
        patch("mount.trio.run"),
        patch("mount.os.makedirs"),
    ):
        main([])
    mock_index.load.assert_called_once()


def test_main_calls_sync_metadata():
    with (
        patch("mount.create_pyonedrive_config_folder"),
        patch("mount.init_onedrive_database"),
        patch("mount.authenticate"),
        patch("mount.MetadataIndex"),
        patch("mount.sync_metadata", return_value=[]) as mock_sync,
        patch("mount.CacheManager"),
        patch("mount.OneDriveFUSE"),
        patch("mount.pyfuse3.init"),
        patch("mount.pyfuse3.close"),
        patch("mount.trio.run"),
        patch("mount.os.makedirs"),
    ):
        main([])
    mock_sync.assert_called_once()


def test_main_inits_pyfuse3():
    with (
        patch("mount.create_pyonedrive_config_folder"),
        patch("mount.init_onedrive_database"),
        patch("mount.authenticate"),
        patch("mount.MetadataIndex"),
        patch("mount.sync_metadata", return_value=[]),
        patch("mount.CacheManager"),
        patch("mount.OneDriveFUSE"),
        patch("mount.pyfuse3.init") as mock_init,
        patch("mount.pyfuse3.close"),
        patch("mount.trio.run"),
        patch("mount.os.makedirs"),
    ):
        main(["--mountpoint", "/tmp/test-od"])
    mock_init.assert_called_once()
    mountpoint_arg = mock_init.call_args[0][1]
    assert mountpoint_arg == "/tmp/test-od"


def test_main_always_closes_fuse_on_exit():
    """pyfuse3.close must be called even when trio.run raises."""
    with (
        patch("mount.create_pyonedrive_config_folder"),
        patch("mount.init_onedrive_database"),
        patch("mount.authenticate"),
        patch("mount.MetadataIndex"),
        patch("mount.sync_metadata", return_value=[]),
        patch("mount.CacheManager"),
        patch("mount.OneDriveFUSE"),
        patch("mount.pyfuse3.init"),
        patch("mount.pyfuse3.close") as mock_close,
        patch("mount.trio.run", side_effect=RuntimeError("crash")),
        patch("mount.os.makedirs"),
    ):
        with pytest.raises(RuntimeError):
            main([])
    mock_close.assert_called_once_with(unmount=True)


def test_main_auth_failure_exits_with_code_1():
    exit_code = _patched_main(auth_raises=True)
    assert exit_code == 1


def test_main_sync_failure_does_not_prevent_mount():
    """If initial sync fails, the mount should proceed with cached data."""
    with (
        patch("mount.create_pyonedrive_config_folder"),
        patch("mount.init_onedrive_database"),
        patch("mount.authenticate"),
        patch("mount.MetadataIndex"),
        patch("mount.sync_metadata", side_effect=Exception("no network")),
        patch("mount.CacheManager"),
        patch("mount.OneDriveFUSE"),
        patch("mount.pyfuse3.init") as mock_init,
        patch("mount.pyfuse3.close"),
        patch("mount.trio.run"),
        patch("mount.os.makedirs"),
    ):
        main([])
    # pyfuse3.init must still be called despite the sync failure
    mock_init.assert_called_once()


def test_main_startup_order():
    """Verify the startup sequence: auth → load → sync → init → run."""
    call_log = []

    with (
        patch("mount.create_pyonedrive_config_folder"),
        patch("mount.init_onedrive_database"),
        patch("mount.authenticate",     side_effect=lambda: call_log.append("auth")),
        patch("mount.MetadataIndex",    side_effect=lambda: _mock_index_factory(call_log)),
        patch("mount.sync_metadata",    side_effect=lambda idx: call_log.append("sync") or []),
        patch("mount.CacheManager"),
        patch("mount.OneDriveFUSE"),
        patch("mount.pyfuse3.init",     side_effect=lambda *a, **kw: call_log.append("fuse_init")),
        patch("mount.pyfuse3.close"),
        patch("mount.trio.run",         side_effect=lambda *a, **kw: call_log.append("trio_run")),
        patch("mount.os.makedirs"),
    ):
        main([])

    assert call_log.index("auth") < call_log.index("load")
    assert call_log.index("load") < call_log.index("sync")
    assert call_log.index("sync") < call_log.index("fuse_init")
    assert call_log.index("fuse_init") < call_log.index("trio_run")


def _mock_index_factory(call_log):
    idx = MagicMock()
    idx.load.side_effect = lambda: call_log.append("load")
    return idx


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------

def test_help_exits_cleanly():
    with pytest.raises(SystemExit) as exc_info:
        parse_args(["--help"])
    assert exc_info.value.code == 0
