# py-onedrive
A Python client for one-way syncing OneDrive to local disk.

## Supported Features

* OAuth2 authentication via Microsoft Graph API
* One-way sync from OneDrive to disk (download only)
* Incremental sync using OneDrive delta API (only changed files are downloaded)
* Deletion sync — files/folders deleted in OneDrive are removed from disk
* Include filter — sync only specific folders
* Exclude filter — skip specific folders
* OneDrive Personal only

## Requirements

* Python 3.x
* A Microsoft account with OneDrive Personal

## Installation

```bash
git clone https://github.com/ccampora/py-onedrive.git
cd py-onedrive
pip install -r requirements.txt
```

## Configuration

All configuration files are stored in `~/.py-onedrive/`.

### Include / Exclude filters

Create `~/.py-onedrive/.py-onedrive-folders` to control which folders are synced.

**Include filter** — sync only the listed folders (and their contents). If the `include` list is empty, all folders are synced (subject to exclusions).

**Exclude filter** — always skip the listed folders, even if they match an include rule.

Filter paths use the OneDrive path format starting with `:/`:

```json
{
    "include": [
        { "path": ":/Documents" },
        { "path": ":/Projects" }
    ],
    "exclude": [
        { "path": ":/Pictures" },
        { "path": ":/Others/01" }
    ]
}
```

If the file does not exist, all folders are synced with no exclusions.

### Sync destination

Files are synced to `~/onedrive` by default. The directory is created automatically on first run.

## Running

From the cloned directory:

```bash
python Onedrive.py
```

On the first run, a browser window will open for Microsoft account login. After authorizing, you will be redirected to a URL like:

```
https://login.microsoftonline.com/common/oauth2/nativeclient?code=M.C544_SN1...
```

Paste either the full URL or just the `code` value into the console. Tokens are saved to `~/.py-onedrive/.py-onedrive-secrets` and refreshed automatically on subsequent runs.

## Running on a schedule (crontab)

Edit your crontab:

```bash
crontab -e
```

Add an entry to run every minute:

```
* * * * * python /path/to/py-onedrive/Onedrive.py >> ~/.py-onedrive/py-onedrive.log 2>&1
```

## Roadmap

### [P0] On-demand virtual filesystem (Files On-Demand)
The top priority feature. Instead of eagerly downloading all files, mount a virtual filesystem using **FUSE** (`pyfuse3`) where files appear in directory listings immediately (metadata only) and content is fetched from OneDrive transparently the first time a file is opened — exactly like Windows OneDrive's Files On-Demand.

The current project already has the required building blocks (auth, item metadata DB, Graph API calls). The main work is replacing the eager sync loop in `Operations.py` with a FUSE driver implementing:

- `readdir()` — list directory contents from the Graph API / local metadata cache
- `getattr()` — return file size and timestamps from the local DB (no download needed)
- `open()` / `read()` — stream file content from `https://graph.microsoft.com/v1.0/me/drive/items/{id}/content`

Trade-offs vs. the current eager sync mode:

| | Eager sync (current) | FUSE on-demand |
|---|---|---|
| Disk usage | Full copy of OneDrive | Only files you actually open |
| Works offline | Yes | No — requires network on file access |
| Runs as cron job | Yes | No — process must stay mounted |
| Complexity | Low | Higher (FUSE driver) |

### Upload / two-way sync
The client is currently download-only. Local changes (new files, edits, deletions) are not pushed back to OneDrive.

### Configurable sync destination
The sync folder is hardcoded to `~/onedrive` in `Globals.py`. It should be settable via a config file or a CLI flag so users are not required to edit source code.

### CLI arguments
No command-line interface exists. Useful flags would include:
- `--dry-run` — show what would be downloaded or deleted without making changes
- `--verbose` / `--debug` — control log level without editing source
- `--config` — point to a non-default config directory

### Rate limit handling
The Microsoft Graph API returns HTTP 429 when rate-limited. The client does not currently handle this and will log an error instead of backing off and retrying.

### OneNote sync
The code detects OneNote packages and creates the corresponding local folder, but the notebook content itself is not downloaded.

### Shared folders
Only the user's own drive is synced. Folders shared by others (which appear under a different drive root) are not supported.

### Error recovery and retry
Failed downloads are logged and skipped. A retry mechanism with exponential back-off would make the client more resilient on slow or unstable connections.

## File layout

| Path | Purpose |
|------|---------|
| `~/.py-onedrive/.py-onedrive-secrets` | OAuth2 access and refresh tokens |
| `~/.py-onedrive/.py-onedrive-folders` | Include / exclude filter config |
| `~/.py-onedrive/.py-onedrive-deltalink` | Delta sync state (resumes where last run left off) |
| `~/.py-onedrive/db/` | Local cache of OneDrive item metadata |
| `~/onedrive/` | Synced files destination |
