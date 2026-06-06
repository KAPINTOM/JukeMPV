# JukeMPV

A minimal, dependency-free YouTube playlist toolkit for the terminal.
Two scripts work together: one builds your personal playlist catalogue, the other launches it in mpv.

![JukeMPV Screenshot](https://github.com/KAPINTOM/JukeMPV/blob/main/images/jukempv%20screenshot.png)

```
add-to-playlist.py   →   playlists.json   →   jukempv.py   →   mpv
      (save)               (catalogue)          (pick & play)
```

No third-party libraries. No accounts. No cloud. Everything lives in a single JSON file you own.

---

## Table of Contents

1. [Requirements](#requirements)
2. [How It Works — The Big Picture](#how-it-works--the-big-picture)
3. [The Catalogue: playlists.json](#the-catalogue-playlistsjson)
4. [add-to-playlist.py — Building Your Catalogue](#add-to-playlistpy--building-your-catalogue)
   - [Usage](#usage)
   - [URL Processing Pipeline](#url-processing-pipeline)
   - [Title Extraction](#title-extraction)
   - [Catalogue Management](#catalogue-management)
   - [CLI Reference](#cli-reference)
5. [jukempv.py — The Launcher](#jukempvpy--the-launcher)
   - [Usage](#usage-1)
   - [Interactive Menu](#interactive-menu)
   - [Playback Speed](#playback-speed)
   - [How mpv Is Launched](#how-mpv-is-launched)
6. [End-to-End Workflow](#end-to-end-workflow)
7. [Troubleshooting](#troubleshooting)
8. [Design Decisions](#design-decisions)

---

## Requirements

| Dependency | Purpose | Install |
|---|---|---|
| **Python 3.10+** | Both scripts | [python.org](https://www.python.org/downloads/) |
| **mpv** | Media playback | See below |
| **yt-dlp** | Used internally by mpv to stream YouTube | See below |

**Installing mpv:**

```bash
# Ubuntu / Debian
sudo apt install mpv ffmpeg yt-dlp

# Fedora
sudo dnf install mpv yt-dlp

# OpenSUSE
sudo zypper install mpv ffmpeg yt-dlp

# Arch Linux
sudo pacman -Sy mpv ffmpeg yt-dlp

# macOS
brew install mpv

# Windows
# Download from https://mpv.io/installation/
```

**Installing yt-dlp** (mpv uses it automatically if present):

```bash
pip install yt-dlp
# or
brew install yt-dlp
```

No Python packages beyond the standard library are required for either script.

---

## How It Works — The Big Picture

The toolkit is intentionally split into two single-responsibility scripts:

**`add-to-playlist.py`** is a curator. You give it a YouTube URL; it fetches the page, extracts the title, cleans the URL, and appends the entry to `playlists.json`. It never touches mpv.

**`jukempv.py`** is a launcher. It reads `playlists.json`, presents an interactive menu, and hands control over to mpv. It never touches YouTube directly.

The shared contract between them is the JSON file — a plain dictionary mapping titles to URLs. This separation means you can edit the catalogue by hand, generate it from another tool, or use `jukempv.py` with any JSON file that matches the format.

---

## The Catalogue: playlists.json

Both scripts read and write the same file. Its format is a flat JSON object where every key is a human-readable name and every value is a YouTube URL:

```json
{
  "Lo-Fi Chill": "https://www.youtube.com/playlist?list=PLxxxxxx",
  "Deep Focus": "https://www.youtube.com/playlist?list=PLyyyyyy",
  "Morning Mix": "https://www.youtube.com/watch?v=zzzzzzzz"
}
```

Rules enforced by `jukempv.py` at load time:

- The file must be valid JSON.
- The top-level value must be an object (not an array or a string).
- Every key and value must be a non-blank string.
- Every value must start with `http://` or `https://`.

The file is created automatically the first time you run `add-to-playlist.py`. You can also create or edit it manually — both scripts handle a missing file gracefully.

**Default location:** the same directory as the scripts. You can override this with the `--json` flag (see CLI reference).

---

## add-to-playlist.py — Building Your Catalogue

This script takes a YouTube URL, resolves its title from the live page, and saves the entry to `playlists.json`. It uses only Python's standard library — no `requests`, no `beautifulsoup4`, no `selenium`.

### Usage

**Interactive** — the script will prompt you:

```bash
python add-to-playlist.py
```

**Non-interactive** — pass the URL directly (useful for scripting):

```bash
python add-to-playlist.py "https://www.youtube.com/playlist?list=PLxxxxxx"
```

### URL Processing Pipeline

Every URL goes through a strict, ordered pipeline before anything is fetched or saved. This ensures that the same content is never stored twice under different keys, regardless of where the link was copied from.

```
Raw input URL
     │
     ▼
1. Validate host
     │  Reject anything that isn't a known YouTube domain.
     │  Accepted hosts:
     │    www.youtube.com, youtube.com, m.youtube.com,
     │    youtu.be, music.youtube.com
     │
     ▼
2. Expand short-links  (youtu.be only)
     │  youtu.be/<id>  →  https://www.youtube.com/watch?v=<id>
     │
     ▼
3. Rewrite music subdomain
     │  music.youtube.com/...  →  www.youtube.com/...
     │  Path and query string are preserved verbatim.
     │
     ▼
4. Strip tracking parameters
     │  Removed: si, pp, feature, ab_channel
     │  Content parameters (v, list, t, …) are kept.
     │
     ▼
5. Remove URL fragment
     │  #t=120 and similar anchors are dropped.
     │
     ▼
Canonical URL  (stored in playlists.json)
```

**Why normalization matters for deduplication:**

Before saving, the script compares the normalized form of the new URL against the normalized form of every URL already in the catalogue. This means all of the following are recognized as the same entry and will not create a duplicate:

```
https://youtu.be/dQw4w9WgXcQ
https://www.youtube.com/watch?v=dQw4w9WgXcQ
https://www.youtube.com/watch?v=dQw4w9WgXcQ&si=abc123&feature=share
https://music.youtube.com/watch?v=dQw4w9WgXcQ
https://m.youtube.com/watch?v=dQw4w9WgXcQ#t=30
```

If a match is found and neither the title nor the URL has changed, the script exits with "Entry already present — no changes made." If only the stored form differs (e.g. the URL was previously saved without normalization), the entry is updated in place.

### Title Extraction

The script does not use the YouTube Data API and requires no API key. Instead, it fetches the page HTML and extracts the title from the embedded JavaScript blobs that YouTube inlines into every page for its own frontend use.

Two blobs are targeted, tried in order:

**`ytInitialPlayerResponse`** — present on video pages. Contains `videoDetails.title`, the same string shown in the video player.

**`ytInitialData`** — present on both video and playlist pages. The script walks several known paths within this object, each corresponding to a different page layout YouTube uses:

| Path | Used for |
|---|---|
| `videoDetails.title` | Video pages (fallback) |
| `metadata.playlistMetadataRenderer.title` | Playlist pages (primary) |
| `contents.twoColumnBrowseResultsRenderer…playlistHeaderRenderer.title` | Playlist pages (alternate layout) |
| `microformat.microformatDataRenderer.title` | Miscellaneous fallback |

The blob extractor does not use regex. It walks the raw HTML character-by-character, tracking brace depth and string-literal boundaries (handling both `"` and `'` delimiters and `\` escape sequences). This correctly handles deeply nested objects where a naïve regex would stop at the first `};` it encounters inside the blob.

**Network behavior:**

The fetcher sends browser-like headers (a real Chrome User-Agent and `Accept-Language: en-US`) so YouTube serves the full page rather than a bot-challenge redirect. Requests are retried up to 3 times with exponential back-off (1.5 s → 3 s → 6 s) for transient failures: rate limiting (HTTP 429), server errors (500, 502, 503, 504), network timeouts, and DNS failures. Permanent client errors (400, 403, 404, etc.) are not retried.

### Catalogue Management

**Loading** — if `playlists.json` does not exist, an empty catalogue is used and the file is created on first save. If the file exists but contains invalid JSON, a backup is written to `playlists.json.bak` and the script starts with an empty catalogue, preserving the broken file for manual inspection rather than silently overwriting it.

**Saving** — writes are atomic. The new content is written to a temporary file in the same directory, then `os.replace()` swaps it into place. On POSIX systems this is a single atomic filesystem operation; a crash or power loss mid-write cannot produce a corrupted file.

### CLI Reference

```
usage: add-to-playlist.py [-h] [--json PATH] [--dry-run] [--verbose] [url]

positional arguments:
  url          YouTube URL to add. If omitted, the script prompts interactively.

options:
  --json PATH  Path to playlists.json. Defaults to the directory containing the script.
  --dry-run    Fetch the title and check for duplicates, but do not write to disk.
               Useful for testing URLs before committing them.
  --verbose    Print debug-level output: normalized URLs, stripped parameters,
               which JSON blob path the title was found in, byte counts, etc.
  -h, --help   Show help and exit.
```

**Exit codes:**

| Code | Meaning |
|---|---|
| `0` | Success (entry added, updated, or already present) |
| `1` | Error (invalid URL, network failure, title not found) |

---

## jukempv.py — The Launcher

This script reads `playlists.json` and presents an interactive terminal menu. After you select a playlist and a playback speed, it hands control to mpv — the Python process disappears entirely and only mpv remains running.

### Usage

```bash
# Use playlists.json next to the script (default)
python jukempv.py

# Use a custom catalogue path
python jukempv.py /path/to/my-playlists.json
```

When bundled as a standalone binary with PyInstaller, the script resolves the config path relative to the executable rather than the source file, so `playlists.json` should sit next to the binary.

### Interactive Menu

On launch the terminal is cleared and the menu is drawn with ANSI color:

```
════════════════════
   🎵  JukeMPV  🎵
════════════════════

  Your Playlists
  ─────────────────────────────────────────
  [1]  Lo-Fi Chill
  [2]  Deep Focus
  [3]  Morning Mix
  [0]  Exit

  Select a playlist:
```

Type the number and press Enter. Entering `0` or pressing `Ctrl+C` exits cleanly. Invalid input (non-numbers, out-of-range numbers) shows an inline error and re-prompts without clearing the screen.

ANSI color is enabled automatically on Windows via the Win32 `SetConsoleMode` API so the menu renders correctly in modern terminals (Windows Terminal, PowerShell 7+). On older terminals that do not support ANSI, the escape codes are visible as raw characters — this is a cosmetic issue only and does not affect functionality.

### Playback Speed

After selecting a playlist, a speed menu is shown:

```
  Playback Speed
  ─────────────────────────────────────────
  [1]   0.75x
  [2]   0.80x
  ...
  [6]   1.00x  ◀ default (Enter)
  ...
  [10]  2.00x
  [11]  Custom speed

  Select a speed (Enter = 1.00x):
```

Pressing Enter without typing anything selects 1.00× (the default). Choosing "Custom speed" prompts for any positive decimal value up to 100.0. The chosen speed is passed directly to mpv's `--speed` flag.

### How mpv Is Launched

mpv is invoked with the following fixed arguments:

| Flag | Effect |
|---|---|
| `--speed=<n>` | Playback speed selected by the user |
| `--ytdl-format=bestaudio` | Stream the best available audio quality only |
| `--shuffle` | Randomize playback order within the playlist |

**On Linux and macOS**, the script calls `os.execvp("mpv", ...)`. This is a true process replacement: the operating system overwrites the Python process image with the mpv binary. After this call there is no Python process in memory — only mpv. The terminal session, signal handling, and resource limits are all inherited cleanly.

**On Windows**, `os.execvp` does not perform a true process replacement (it is emulated by the C runtime and leaves the Python process alive in a waiting state). The script falls back to `subprocess.run`, waits for mpv to exit, and then calls `sys.exit()` with mpv's return code.

If mpv is not installed or not on the `PATH`, a helpful error message is printed with platform-specific installation instructions and the script exits with code 1.

---

## End-to-End Workflow

**Step 1 — Add a playlist to your catalogue:**

```bash
python add-to-playlist.py "https://music.youtube.com/playlist?list=PLn3SE9EUUhRuV99mayFc6h7-OsC9AKuZb"
```

Output:
```
INFO: Normalized URL: https://www.youtube.com/playlist?list=PLn3SE9EUUhRuV99mayFc6h7-OsC9AKuZb
INFO: Title: Lo-Fi Chill Beats
INFO: Saved: "Lo-Fi Chill Beats" → https://www.youtube.com/playlist?list=PLn3SE9EUUhRuV99mayFc6h7-OsC9AKuZb
INFO: Catalogue: /path/to/playlists.json  (1 entries total)
```

**Step 2 — Repeat for as many playlists as you like.**

**Step 3 — Launch JukeMPV:**

```bash
python jukempv.py
```

**Step 4 — Pick a playlist and speed.** mpv opens, streams audio, and shuffles the playlist. Press `q` to quit mpv.

**Inspect or manually edit the catalogue at any time:**

```bash
cat playlists.json
```

```json
{
  "Lo-Fi Chill Beats": "https://www.youtube.com/playlist?list=PLn3SE9EUUhRuV99mayFc6h7-OsC9AKuZb",
  "Deep Focus": "https://www.youtube.com/playlist?list=PLyyyyyyyyyy"
}
```

---

## Troubleshooting

**"Could not retrieve title. Check the URL and your connection."**

YouTube occasionally rate-limits automated requests. The script will retry automatically up to 3 times. If it still fails, wait a minute and try again. Running with `--verbose` shows which retry attempt is in progress and the HTTP status codes received.

**"mpv not found."**

mpv must be on your system `PATH`. Verify with `which mpv` (Linux/macOS) or `where mpv` (Windows). Install it using the instructions in the Requirements section.

**The menu shows garbled characters like `\033[1m`.**

Your terminal does not support ANSI escape codes. Try Windows Terminal or any modern terminal emulator. The garbled output is cosmetic only — the script still functions correctly.

**"Catalogue is malformed. A backup was saved to playlists.json.bak."**

Your `playlists.json` contains invalid JSON. The original file has been preserved as `playlists.json.bak`. Open the `.bak` file in a text editor, fix the syntax, and rename it back.

**A URL I already added is being added again.**

Run `add-to-playlist.py` with `--verbose` to see what normalized URL the script is computing. If the normalized forms differ (e.g. due to a different `list=` parameter), the entries are genuinely different.

**`--dry-run` says "Would add" but the entry already exists.**

`--dry-run` skips writing but still performs the duplicate check. If you see "Would add" it means the entry is genuinely new. If you see "Entry already present", no write would have happened regardless.

---

## Design Decisions

**No third-party dependencies.** Both scripts use only the Python standard library. There is nothing to install beyond Python itself and mpv. This makes the toolkit trivially portable and immune to dependency rot.

**`os.execvp` instead of `subprocess`.** On POSIX, replacing the process rather than spawning a child means there is no orphaned launcher consuming memory or file descriptors while mpv runs. It also means mpv inherits the terminal session directly, so keyboard shortcuts and signal handling work exactly as if you had typed `mpv ...` yourself.

**Atomic writes.** Writing to a temp file and renaming protects against a corrupted `playlists.json` if the process is killed or the system loses power mid-write. The rename is an atomic operation on all major operating systems.

**Character-by-character JSON blob extraction.** Regex approaches that match `{...}` fail on any JSON object that contains `};` inside a string value — a common occurrence in YouTube's embedded data. Walking the string with explicit brace-depth tracking and string-boundary detection is slightly more code but is correct in all cases.

**Separate scripts, shared JSON.** Splitting the curator and the launcher into two programs keeps each one small and easy to reason about. It also means you can replace either half independently — for example, you could write a different launcher that reads the same `playlists.json`, or populate the file from a different source entirely.