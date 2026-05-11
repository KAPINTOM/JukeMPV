# JukeMPV

A minimal terminal launcher that lets you pick a YouTube playlist and hand it off directly to `mpv` — no browser, no background process, no leftover memory.

![JukeMPV Screenshot](https://github.com/KAPINTOM/JukeMPV/blob/main/images/jukempv%20screenshot.png)

---

## How It Works

JukeMPV reads a `playlists.json` file, displays a numbered colour-coded menu in your terminal, and — once you make a selection — calls `os.execvp()` to **replace itself** with the `mpv` process. When `mpv` quits, you are back at your shell prompt with nothing left running in the background.

---

## Features

- **Zero lingering processes** — the launcher replaces itself with `mpv` via `os.execvp()`; nothing stays in memory after launch.
- **Simple JSON config** — a flat `{ "name": "url" }` object maps menu labels to YouTube URLs or any URL that `yt-dlp` understands.
- **Colour terminal menu** — ANSI-styled, numbered list with bold highlights for fast navigation.
- **Shuffle by default** — playlists are passed to `mpv` with `--shuffle`; easy to disable.
- **Audio-optimised** — uses `--ytdl-format=bestaudio`; video can be re-enabled with a one-line change.
- **Cross-platform ANSI support** — detects Windows and enables ANSI escape codes in `cmd`/`PowerShell` automatically.
- **Graceful interrupt handling** — `Ctrl+C` and `Ctrl+D` print a clean exit message instead of a traceback.
- **Companion helper script** — `add-to-playlist.py` fetches a video or playlist title from YouTube and appends it to `playlists.json` automatically.

---

## Requirements

| Dependency | Notes |
|---|---|
| Python 3.7+ | No third-party packages required |
| [mpv](https://mpv.io/) | Must be on `$PATH` |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | Used internally by `mpv` for YouTube streaming |

> Designed and tested on GNU/Linux. Behaviour on Windows and macOS is not guaranteed.

---

## Installation

### From source

```bash
git clone https://github.com/KAPINTOM/JukeMPV
cd JukeMPV
python mpv_launcher.py
```

### Pre-compiled GNU/Linux binary

```bash
chmod +x ./jukempv
./jukempv
```

---

## Configuration

Create `playlists.json` in the same directory as the script or binary. Pass a custom path as a command-line argument to override this.

**Format** — a flat JSON object; keys are the labels shown in the menu, values are URLs:

```json
{
  "tvtcbct":               "https://youtube.com/playlist?list=PLn3SE9EUUhRt-HcE9CnDqo1HXeRkFQpMj",
  "black metal":           "https://youtube.com/playlist?list=PLn3SE9EUUhRsbo3gy0FHpuXj9wfppudcK",
  "chinese hits":          "https://youtube.com/playlist?list=PLn3SE9EUUhRvHE9iuBP4oQ550t0StRx8V",
  "pure buckethead":       "https://youtube.com/playlist?list=PLn3SE9EUUhRvgJCb5aa4U3DmU5GHKKBXc",
  "垃圾音乐":              "https://youtube.com/playlist?list=PLn3SE9EUUhRv2_OSTlCgc70_RTU6gDl1D",
  "Fav Breaking Benjamin": "https://youtube.com/playlist?list=PLn3SE9EUUhRskCL5C4acyi-fwpWp0OUX8",
  "The Doors Greatest Hits": "https://youtu.be/4U3eYkvY9pE"
}
```

- Both playlist URLs and single video URLs are supported.
- Any URL that `yt-dlp`/`mpv` can handle works.
- Entries with blank names or blank URLs are rejected on load with a descriptive error.
- UTF-8 names (including CJK characters) are fully supported.

---

## Usage

```bash
# Default: looks for playlists.json next to the script/binary
jukempv

# Custom config path
jukempv /path/to/my-playlists.json
```

A menu appears:

```
════════════════════
   🎵  JukeMPV  🎵
════════════════════

  Your Playlists
  ─────────────────────────────────────
  [1]  tvtcbct
  [2]  black metal
  [3]  chinese hits
  [4]  pure buckethead
  [5]  垃圾音乐
  [6]  Fav Breaking Benjamin
  [7]  The Doors Greatest Hits
  [0]  Exit

  Select a playlist:
```

Enter a number and press Enter. The launcher prints a brief message and hands control to `mpv`. Press `q` inside `mpv` to quit and return to your shell.

---

## Adding Playlists (`add-to-playlist.py`)

The companion script fetches a YouTube video or playlist title automatically and saves the entry to `playlists.json` — no manual editing needed.

```bash
python add-to-playlist.py
# Enter YouTube video or playlist URL: <paste URL>
# Added / updated: "Fav Breaking Benjamin" -> https://youtube.com/playlist?list=...
```

**What it does internally:**

1. Validates that the URL is a recognised YouTube hostname.
2. Strips tracking parameters (e.g. `si=…`) from the URL before storing it.
3. Fetches the page HTML with browser-like headers to avoid bot challenges.
4. Extracts the title by parsing the embedded `ytInitialPlayerResponse` or `ytInitialData` JavaScript blobs — no external libraries.
5. Appends or updates the entry in `playlists.json`, preserving existing entries.
6. If `playlists.json` is corrupted, backs it up to `playlists.json.bak` before overwriting.

---

## Customising mpv Flags

The `mpv_args` list in `main()` inside `mpv_launcher.py` controls every flag passed to `mpv`:

```python
mpv_args = [
    "--ytdl-format=bestaudio",   # audio only; change to "bestvideo+bestaudio/best" for video
    "--shuffle",                  # remove this line to disable shuffle
    url,
]
```

Any flag that `mpv` accepts can be added here.

---

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| `mpv: command not found` | Install `mpv` and ensure it is on `$PATH` |
| `Config file not found` | Create `playlists.json` in the script directory or pass a path |
| `JSON parse error` | Check for trailing commas, unquoted keys, or mismatched brackets |
| `mpv` quits immediately | The URL may be private or geo-restricted — test with `mpv <URL>` directly |
| Shuffle not working | Confirm your `mpv` version supports `--shuffle`; it requires `mpv ≥ 0.35` |
| Title not found (`add-to-playlist.py`) | YouTube may have changed its page structure; the URL might be private |
| HTTP 429 / 403 (`add-to-playlist.py`) | YouTube is rate-limiting the request; wait and retry |

---

## Project Structure

```
JukeMPV/
├── mpv_launcher.py      # Main launcher — menu, input, os.execvp into mpv
├── add-to-playlist.py   # Helper — fetch YouTube title and append to JSON
├── playlists.json       # Your playlist config (create this yourself)
└── jukempv              # Optional pre-compiled GNU/Linux binary
```

---

## License

MIT — use, modify, and distribute freely.

## Contributing

Issues and pull requests are welcome. The goal is a small, focused tool — keep changes minimal and purposeful.
