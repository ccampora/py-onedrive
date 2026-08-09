# py-onedrive

A two-way OneDrive client for Linux built on FUSE. OneDrive is mounted as a virtual filesystem — all files and folders appear instantly from a local metadata index, file content is fetched transparently on first open, and local changes (create, edit, delete, rename) are synced back to OneDrive automatically.

## Features

- **Read**: on-demand download — files are fetched only when opened, not on directory browse
- **Write**: create, edit, delete, and rename files and folders — changes are uploaded to OneDrive on close
- File managers and thumbnail renderers are blocked from triggering downloads (detected by process name)
- Editor temp files (vim swap, Office lock files, `.tmp`) are silently redirected to `/tmp` and never uploaded
- Remote changes appear within ~30 seconds via background delta polling
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

### From source

```bash
git clone https://github.com/ccampora/py-onedrive.git
cd py-onedrive
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Arch Linux

All dependencies (`python-trio`, `python-requests`, `python-pyfuse3`, `fuse3`) are in the official `extra` repo.

**One-off install** — build and install directly with `makepkg`:

```bash
git clone https://github.com/ccampora/py-onedrive.git
cd py-onedrive/packaging/arch
makepkg -si
```

**Repo install (recommended for staying updated)** — publish releases to a local pacman repo so `pacman -Syu` picks up new versions like any other package:

```bash
# One-time setup: create the repo dir and register it with pacman.
# It must live outside $HOME — pacman's sandboxed download user (alpm)
# can't traverse a private home directory to reach a file:// repo under it.
sudo mkdir -p /srv/pkgrepo && sudo chown "$USER:$USER" /srv/pkgrepo

sudo tee -a /etc/pacman.conf > /dev/null << 'EOF'

[py-onedrive-local]
SigLevel = Optional TrustAll
Server = file:///srv/pkgrepo
EOF

# Build + publish the current release
bash packaging/arch/build-arch.sh

# Install / update
sudo pacman -Sy py-onedrive
```

To pick up a new release, re-run `bash packaging/arch/build-arch.sh X.Y.Z` after `git tag vX.Y.Z && git push --tags`, then `sudo pacman -Syu`.

## Authentication

On the first run a browser window opens for Microsoft account login. After authorizing you are redirected to a URL like:

```
https://login.microsoftonline.com/common/oauth2/nativeclient?code=M.C544_SN1...
```

Paste the full URL or just the `code` value into the console. Tokens are saved to `~/.py-onedrive/.py-onedrive-secrets` and refreshed automatically on subsequent runs.

## Usage

### Start / restart

```bash
./start.sh
```

`start.sh` handles the full lifecycle: kills any existing mount process, cleans up stale FUSE transport endpoints, creates the mountpoint if needed, then launches `mount.py` and waits to confirm it mounted successfully.

Extra flags are passed through to `mount.py`:

```bash
./start.sh --debug
./start.sh --poll-interval 60
```

### Manual start

```bash
source .venv/bin/activate
python mount.py
```

```
Options:
  --mountpoint DIR    Mount point (default: ~/Onedrive)
  --poll-interval N   Seconds between metadata syncs (default: 30)
  --max-downloads N   Max concurrent file downloads (default: 4)
  --debug             Verbose logging and FUSE debug mode
```

### Unmount

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

### Temp file handling

Editor temp files are intercepted at `create()` time and redirected entirely to `/tmp`. They are invisible in directory listings and never reach OneDrive. Filtered patterns:

- Vim: `.*.swp`, `.*.swx`, numeric write-probe files (e.g. `4913`)
- Generic: `*.tmp`, files ending with `~`
- Office: `~$*` lock files, `.~lock.*` LibreOffice locks

### Remote changes

The background poller calls the OneDrive delta API every 30 seconds. When changes are detected, the local metadata index is updated and the affected directory inodes are invalidated so `ls` reflects the change immediately on the next call.

### Running as a systemd service

```bash
cp onedrive.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now onedrive
systemctl --user status onedrive
```

## COSMIC Desktop Applet

A native panel applet for the COSMIC desktop that shows sync status, pending files, and provides quick actions (Sync Now, Pause, Resume). It talks to the daemon over the control socket (`~/.py-onedrive/control.sock`), so install the daemon first — from source or via the Arch package (see [Installation](#installation) above) — before building the applet.

### Building

Requires Rust (stable) and the COSMIC SDK dependencies:

```bash
cd applet/cosmic
cargo build --release
```

### Installing

```bash
# Install the binary
cp applet/cosmic/target/release/cosmic-onedrive-applet ~/.local/bin/

# Install the desktop entry so the panel can discover the applet
cp /usr/share/applications/com.github.ccampora.OneDriveApplet.desktop ~/.local/share/applications/
```

If you don't already have the desktop entry, create it:

```bash
mkdir -p ~/.local/share/applications
cat > ~/.local/share/applications/com.github.ccampora.OneDriveApplet.desktop <<'EOF'
[Desktop Entry]
Name=OneDrive Applet
Comment=OneDrive sync status applet
Exec=cosmic-onedrive-applet
Icon=cloud
Type=Application
NoDisplay=true
X-CosmicApplet=true
Categories=System;
EOF
```

### Adding to the panel

Open **Settings → Desktop → Panel → Applets** and add "OneDrive Applet" to the right wing. The panel will manage the applet lifecycle automatically (start on login, restart on crash).

### Icons

The panel icon changes based on connection state:

| State | Icon |
|-------|------|
| Connected / idle | Light gray cloud |
| Syncing / uploading / downloading | Cloud with sync arrows |
| Paused | Cloud with pause bars |
| Error | Cloud with warning triangle |
| Disconnected | Dimmed cloud with red X |

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
