# py-onedrive

A Python client for OneDrive on Linux, with two modes:

- **Eager sync** (`Onedrive.py`) — downloads all files to a local folder, stays in sync via cron
- **Files On-Demand** (`mount.py`) — mounts OneDrive as a virtual FUSE filesystem; files appear instantly from metadata and content is fetched transparently only when a file is opened

## Features

- OAuth2 authentication via Microsoft Graph API
- Incremental sync using the OneDrive delta API (only changes are fetched)
- Deletion sync — items deleted in OneDrive are removed locally
- Files On-Demand FUSE mount with:
  - Zero downloads on directory browsing (file managers and thumbnail renderers are blocked)
  - On-demand download triggered only when a file is explicitly opened by a user application
  - Background metadata polling to pick up remote changes
  - Auto-unmount on process exit (no zombie mounts requiring a reboot)
- OneDrive Personal only

## Requirements

- Python 3.10+
- `fuse3` and `libfuse3-dev` system packages
- A Microsoft account with OneDrive Personal

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

Both modes share the same auth flow. On the first run, a browser window opens for Microsoft account login. After authorizing you are redirected to a URL like:

```
https://login.microsoftonline.com/common/oauth2/nativeclient?code=M.C544_SN1...
```

Paste the full URL or just the `code` value into the console. Tokens are saved to `~/.py-onedrive/.py-onedrive-secrets` and refreshed automatically on subsequent runs.

## Files On-Demand (FUSE mount)

```bash
python mount.py
```

OneDrive is mounted at `~/Onedrive` by default. All 50 000+ items appear immediately in directory listings — no content is downloaded until you open a file.

```
Options:
  --mountpoint DIR      Mount point (default: ~/Onedrive)
  --poll-interval N     Seconds between metadata syncs (default: 300)
  --max-downloads N     Max concurrent downloads (default: 4)
  --debug               Verbose logging and FUSE debug mode
```

To unmount:

```bash
fusermount3 -u ~/Onedrive
# or Ctrl+C in the terminal where mount.py is running
```

### How file manager browsing works

Opening `~/Onedrive` in a file manager (Cosmic Files, Nautilus, Thunar, etc.) lists all files from the local metadata index — no network calls, no downloads. File managers and thumbnail renderers that probe file content are detected by process name and blocked with `ENODATA`. Downloads only happen when you open a file with an application (Evince, LibreOffice, etc.).

### Running as a systemd service

```bash
cp onedrive.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now onedrive
systemctl --user status onedrive
```

## Eager sync mode

```bash
python Onedrive.py
```

Downloads all OneDrive content to `~/onedrive`. Suitable for running as a cron job:

```bash
crontab -e
# add:
* * * * * /path/to/py-onedrive/.venv/bin/python /path/to/py-onedrive/Onedrive.py >> ~/.py-onedrive/py-onedrive.log 2>&1
```

## File layout

| Path | Purpose |
|------|---------|
| `~/.py-onedrive/.py-onedrive-secrets` | OAuth2 tokens |
| `~/.py-onedrive/.py-onedrive-deltalink` | Delta sync cursor |
| `~/.py-onedrive/.py-onedrive-inodes` | Stable inode mapping for FUSE |
| `~/.py-onedrive/db/` | Item metadata cache (one JSON file per item) |
| `~/.py-onedrive/cache/` | Downloaded file content cache |
| `~/Onedrive/` | FUSE mount point |
| `~/onedrive/` | Eager sync destination |

## Roadmap

- **Upload / two-way sync** — local changes are not pushed back to OneDrive
- **Shared folders** — only the user's own drive is supported
- **OneNote** — notebook content is not downloaded
- **Sparse / streaming reads** — large files are fully downloaded before the first byte is returned; range requests would allow faster first-open
- **Cache eviction policy** — the local content cache grows unboundedly; LRU eviction would bound disk usage
