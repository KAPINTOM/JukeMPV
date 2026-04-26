# JukeMPV

**JukeMPV** is a minimal, one‑shot terminal menu for launching YouTube playlists in `mpv`.  
You pick a playlist, JukemPV hands over control to `mpv` – no background process, no leftover memory. Just your music (or videos) and a clean shell afterwards.

This tool was designed and tested for being used in a linux terminal, its behaviour on windows and other systems is unknown.

I created this tool because I hated having the browser open in background just playing music, so much precious memory and cpu resources wasted

![JukeMPV Screenshot](https://github.com/KAPINTOM/JukeMPV/blob/main/images/jukempv%20screenshot.png) <!-- optional: add a terminal screenshot later -->

## Features

- 🎵 **One‑shot launch** – the script replaces itself with `mpv` using `os.execvp()`
- 📋 **Simple JSON config** – map playlist names to YouTube URLs
- 🎨 **Coloured terminal menu** – easy to read, fast to navigate
- 🔀 **Shuffles playlists** by default (can be removed from the command line)
- 🔊 **Audio‑optimised** – uses `--ytdl-format=bestaudio` (but video can be re‑enabled)
- 🧹 **No daemon, no tray icon** – after mpv quits, you’re back to your shell

## Requirements

- **Python 3.6+** (no external libraries needed)
- **[mpv](https://mpv.io/)** installed and available in `$PATH`
- **[yt-dlp](https://github.com/yt-dlp/yt-dlp)** (mpv uses it internally – install separately or via your package manager)

## Usage
Is recommended using the pre compiled binary for GNU/Linux, but you can compile it yourself using python, you don't need any extra python library

### From Source

Clone the repository or download `jukempv.py`:

```bash
git clone https://github.com/KAPINTOM/JukeMPV
cd jukeMPV
python jukempv.py
```

### From GNU/Linux Binary

```bash
chmod +x ./jukempv
./jukempv
```

## Configuration

Create a file named `playlists.json` in the directory you run the script from, or pass a custom path.

**Format** – a flat JSON object where each key is a playlist name (shown in the menu) and each value is the full YouTube URL:

```json
{
  "tvtcbct": "https://youtube.com/playlist?list=PLn3SE9EUUhRt-HcE9CnDqo1HXeRkFQpMj&si=o-ie2DjaIBNYfPjY",

  "black metal": "https://youtube.com/playlist?list=PLn3SE9EUUhRsbo3gy0FHpuXj9wfppudcK&si=rqsKYrHkna2ZbnMD",
  
  "chinnese hits": "https://youtube.com/playlist?list=PLn3SE9EUUhRvHE9iuBP4oQ550t0StRx8V&si=Thz6rEHuXSlRqVaE",

  "pure buckethead": "https://youtube.com/playlist?list=PLn3SE9EUUhRvgJCb5aa4U3DmU5GHKKBXc&si=IMqtF_C7sS2tdn3f",

  "垃圾音乐": "https://youtube.com/playlist?list=PLn3SE9EUUhRv2_OSTlCgc70_RTU6gDl1D&si=xGydFaDPN7eIXOJA",

  "Fav Breaking Benjamin": "https://youtube.com/playlist?list=PLn3SE9EUUhRskCL5C4acyi-fwpWp0OUX8&si=pK45gLVHBiVIv0OG",

  "The Doors Greatest Hits": "https://youtu.be/4U3eYkvY9pE?si=wwIhwgQS9oefxNr5"
}
```

> 💡 You can also use single YouTube videos or any URL that `yt-dlp` / `mpv` understands – the script treats everything as a single argument.

> Fun fact, all the youtube playlists were also created by me.

## Usage

Run JukeMPV:

```bash
jukempv                   # uses ./playlists.json
jukempv /path/to/config.json
```

A numbered menu will appear. Enter the number of the playlist (or `0` to exit):

```
╔══════════════════════════════════════╗
║   🎵  mpv YouTube Launcher           ║
╚══════════════════════════════════════╝

  Your Playlists
  ─────────────────────────────────────
  [1]  tvtcbct
  [2]  black metal
  [3]  chinnese hits
  [4]  pure buckethead
  [5]  垃圾音乐
  [6]  Fav Breaking Benjamin
  [7]  The Doors Greatest Hits
  [0]  Exit

  Select a playlist: 6
```

After you choose, JukemPV prints a short message and **replaces itself** with `mpv`. The terminal now shows mpv’s own output. Press `q` to quit mpv and return to your shell.

## Customising mpv behaviour

The script currently launches mpv with:

```bash
mpv --ytdl-format=bestaudio --shuffle <URL>
```

To change these flags (e.g., enable video, disable shuffle, add `--no-keep-open`), edit the `mpv_args` list inside the `main()` function. For example, to show video:

```python
mpv_args = [
    "--ytdl-format=bestvideo+bestaudio/best",   # higher quality, includes video
    "--shuffle",
    url
]
```

## Troubleshooting

| Problem                          | Likely fix                                                       |
|----------------------------------|------------------------------------------------------------------|
| `mpv: command not found`         | Install mpv (see Requirements)                                   |
| `Config file not found`          | Create `playlists.json` or pass the correct path                 |
| `JSON parse error`               | Check your JSON syntax (trailing commas, quotes, etc.)           |
| mpv plays audio but no shuffle   | The `--shuffle` flag is present – if it still doesn’t shuffle, check your mpv version |
| mpv quits immediately            | The YouTube playlist might be private or unreachable – test with `mpv <URL>` directly |

## Why “JukeMPV”?

The name blends **jukebox** (pick a playlist and let it play) with **mpv** (the media player that does the heavy lifting). The “em” hints at “EM” (Electro‑Mechanical) – a nod to old jukeboxes.

## License

MIT License – use, modify, and share freely.

## Contributing

Issues and pull requests are welcome. Keep it minimal – the goal is a tiny, focused launcher.
