# py-onedrive

A two-way OneDrive client for Linux built on FUSE. OneDrive is mounted as a virtual filesystem — all files and folders appear instantly from a local metadata index, file content is fetched transparently on first open, and local changes (create, edit, delete, rename) are synced back to OneDrive automatically.

## Features

- **Read**: on-demand download — files are fetched only when opened, not on directory browse
- **Write**: create, edit, delete, and rename files and folders — changes are uploaded to OneDrive on close
- File managers and thumbnail renderers are blocked from triggering downloads (detected by process name)
- Incremental metadata sync via the OneDrive delta API — only changes are fetched
- Background metadata polling keeps the directory tree up to date
- Auto-unmount on process exit — no zombie mounts requiring a reboot
- OAuth2 authentication with automatic token refresh
- OneDrive Personal only

## Requirements

- Python 3.10+
- `fuse3` and `libfuse3-dev` system packages

```bash
sudo apt install fuse3 libfuse3-dev   # Debian/Ubuntu/Pop!_OS
```

## Installation

```bash
git clone https://github.com/ccampora/py-onedrive.git
cd py-onedrive
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Authentication

On the first run a browser window opens for Microsoft account login. After authorizing you are redirected to a URL like:

```
https://login.microsoftonline.com/common/oauth2/nativeclient?code=M.C544_SN1...
```

Paste the full URL or just the `code` value into the console. Tokens are saved to `~/.py-onedrive/.py-onedrive-secrets` and refreshed automatically on subsequent runs.

## Usage

```bash
source .venv/bin/activate
python mount.py
```

OneDrive is mounted at `~/Onedrive`. All files and folders appear immediately — no content is downloaded until you open a file.

```
Options:
  --mountpoint DIR    Mount point (default: ~/Onedrive)
  --poll-interval N   Seconds between metadata syncs (default: 300)
  --max-downloads N   Max concurrent file downloads (default: 4)
  --debug             Verbose logging and FUSE debug mode
```

To unmount:

```bash
fusermount3 -u ~/Onedrive
# or Ctrl+C if running in the foreground
```

## How it works

### Reading files

Directory listings are served entirely from the local metadata index — no network calls on browse. When a file is opened with an application, it is downloaded from OneDrive and cached locally. Subsequent opens are instant from the local cache.

File managers and thumbnail renderers (`cosmic-files`, `nautilus`, `evince-thumbnai`, `tumbler`, etc.) are detected by process name and blocked from triggering downloads. Only applications that explicitly open a file (Evince, LibreOffice, a text editor, etc.) cause a fetch.

### Writing files

Changes made inside `~/Onedrive` are synced back to OneDrive:

| Operation | Behaviour |
|-----------|-----------|
| Create file | Uploaded to OneDrive when the file is closed |
| Edit file | Current content downloaded to a temp file, uploaded on close |
| Create folder | Created on OneDrive immediately |
| Delete file or folder | Deleted from OneDrive immediately |
| Rename / move | Updated on OneDrive immediately via a single API call |

New files and folders appear in directory listings immediately after creation — no need to refresh.

### Running as a systemd service

```bash
cp onedrive.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now onedrive
systemctl --user status onedrive
```

## File layout

| Path | Purpose |
|------|---------|
| `~/.py-onedrive/.py-onedrive-secrets` | OAuth2 access and refresh tokens |
| `~/.py-onedrive/.py-onedrive-deltalink` | Delta sync cursor (resumes from last known state) |
| `~/.py-onedrive/.py-onedrive-inodes` | Stable inode mapping (survives process restarts) |
| `~/.py-onedrive/db/` | Item metadata cache — one JSON file per OneDrive item |
| `~/.py-onedrive/cache/` | Downloaded file content cache |
| `~/Onedrive/` | FUSE mount point |

## Roadmap

- **Shared folders** — only the user's own drive is currently supported
- **Large file uploads** — files over ~150 MB should use the Graph API upload session for reliability
- **Cache eviction** — the content cache grows unboundedly; an LRU policy would bound disk usage
- **Conflict resolution** — no handling when the same file is modified both locally and remotely
