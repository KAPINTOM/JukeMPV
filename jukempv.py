#!/usr/bin/env python3
"""
mpv-launcher — simple YouTube playlist quick-launcher for mpv
Shows a menu, then replaces itself with mpv — no launcher stays in memory.
"""

from __future__ import annotations  # FIX 1: enables dict[str, str] on Python 3.7/3.8

import json
import os
import sys
from pathlib import Path

# ── ANSI colours ─────────────────────────────────────────────────────────────
class c:
    reset  = "\033[0m"
    bold   = "\033[1m"
    dim    = "\033[2m"
    red    = "\033[31m"
    green  = "\033[32m"
    yellow = "\033[33m"
    blue   = "\033[34m"
    cyan   = "\033[36m"
    white  = "\033[97m"

# FIX 2: enable ANSI on Windows so colours and clear() work in cmd/PowerShell
if sys.platform == "win32":
    import ctypes
    ctypes.windll.kernel32.SetConsoleMode(
        ctypes.windll.kernel32.GetStdHandle(-11), 7
    )

def clr(*parts) -> str:
    return "".join(parts) + c.reset

# ── Helpers ───────────────────────────────────────────────────────────────────
def clear():
    # FIX 3: use the correct clear command per platform
    if sys.platform == "win32":
        os.system("cls")
    else:
        print("\033[2J\033[H", end="")

def header():
    print(clr(c.bold, c.blue,
        "╔════════════════════╗\n"
        "║   🎵  JukeMPV  🎵   ║\n"
        "╚════════════════════╝"))
    print()

def ok(msg: str):
    print(clr(c.green, c.bold, "[✓] ") + msg)

def info(msg: str):
    print(clr(c.cyan, c.bold, "[i] ") + msg)

def err(msg: str):
    print(clr(c.red, c.bold, "[error] ") + clr(c.red, msg), file=sys.stderr)

def prompt_int(prompt: str, lo: int, hi: int) -> int:
    while True:
        try:
            raw = input(clr(c.bold, c.yellow, prompt))
            val = int(raw.strip())
            if lo <= val <= hi:
                return val
        except ValueError:
            pass
        except (EOFError, KeyboardInterrupt):
            # FIX 4: handle Ctrl+C / Ctrl+D cleanly instead of dumping a traceback
            print(clr(c.dim, "\nGoodbye!\n"))
            sys.exit(0)
        print(clr(c.red, f"  Please enter a number between {lo} and {hi}."))

# ── Config loading ────────────────────────────────────────────────────────────
def load_playlists(path: Path) -> dict[str, str]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise SystemExit(
            clr(c.red, f"Config file not found: {path}\n") +
            clr(c.dim, "Create it or pass a custom path as argument.")
        )

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise SystemExit(clr(c.red, f"JSON parse error: {e}"))

    if not isinstance(data, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in data.items()
    ):
        raise SystemExit(clr(c.red, "Invalid format. Expected a flat object of {name: url}."))

    # FIX 5: reject entries with blank names or blank URLs
    invalid = [k for k, v in data.items() if not k.strip() or not v.strip()]
    if invalid:
        bad = ", ".join(repr(k) for k in invalid)
        raise SystemExit(clr(c.red, f"Playlist entries with empty name or URL: {bad}"))

    return data

# ── Main (one-shot, then exec into mpv) ───────────────────────────────────────
def main():
    if len(sys.argv) > 1:
        config_path = Path(sys.argv[1])
    else:
        # FIX 6: resolve relative to the script's own directory, not the shell's CWD
        config_path = Path(__file__).parent / "playlists.json"

    playlists = load_playlists(config_path)

    if not playlists:
        raise SystemExit(clr(c.red, "No playlists found."))

    clear()
    header()

    print(clr(c.bold, c.white, "  Your Playlists"))
    print(clr(c.dim,           "  ─────────────────────────────────────"))

    names = list(playlists.keys())
    for i, name in enumerate(names, 1):
        print(f"  {clr(c.bold, c.cyan, f'[{i}]')}  {clr(c.white, name)}")

    print(f"  {clr(c.bold, c.red, '[0]')}  Exit\n")

    choice = prompt_int("  Select a playlist: ", 0, len(names))
    if choice == 0:
        print(clr(c.dim, "\nGoodbye!\n"))
        sys.exit(0)

    selected_name = names[choice - 1]
    url = playlists[selected_name]

    # Build mpv command arguments (excluding the program name itself)
    mpv_args = [
        "--ytdl-format=bestaudio",
        # "--no-video",   # keep video if available (you can toggle this)
        "--shuffle",
        url,
    ]

    # Print a brief launch message before we hand over
    print(clr(c.green, c.bold, f"\nLaunching {selected_name}..."))
    print(clr(c.dim, "(The launcher will be replaced by mpv — press 'q' to quit mpv)\n"))

    # Replace the current process with mpv
    try:
        os.execvp("mpv", ["mpv"] + mpv_args)
    except FileNotFoundError:
        err("mpv not found. Install it with your package manager.")
        sys.exit(1)
    except OSError as e:
        # FIX 7: catch broader OS errors (permissions, bad executable, etc.)
        err(f"Failed to launch mpv: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
