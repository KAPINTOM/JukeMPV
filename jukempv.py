#!/usr/bin/env python3
"""
mpv-launcher — simple YouTube playlist quick-launcher for mpv
Shows a menu, then replaces itself with mpv — no launcher stays in memory.
"""

from __future__ import annotations  # enables dict[str, str] on Python 3.7/3.8

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

# Enable ANSI on Windows so colours and clear() work in cmd/PowerShell
if sys.platform == "win32":
    import ctypes
    try:
        ctypes.windll.kernel32.SetConsoleMode(
            ctypes.windll.kernel32.GetStdHandle(-11), 7
        )
    except Exception:
        pass  # non-fatal: colours just won't render

def clr(*parts) -> str:
    return "".join(parts) + c.reset

# ── Helpers ───────────────────────────────────────────────────────────────────
def clear():
    if sys.platform == "win32":
        os.system("cls")
    else:
        print("\033[2J\033[H", end="")

def header():
    print(clr(c.bold, c.blue,
        "════════════════════\n"
        "   🎵  JukeMPV  🎵   \n"
        "════════════════════"))
    print()

def ok(msg: str):
    print(clr(c.green, c.bold, "[✓] ") + msg)

def info(msg: str):
    print(clr(c.cyan, c.bold, "[i] ") + msg)

def err(msg: str):
    print(clr(c.red, c.bold, "[error] ") + clr(c.red, msg), file=sys.stderr)

def _goodbye():
    print(clr(c.dim, "\nGoodbye!\n"))
    sys.exit(0)

def prompt_int(prompt: str, lo: int, hi: int) -> int:
    """Prompt for an integer in [lo, hi]. Loops on bad input."""
    while True:
        try:
            raw = input(clr(c.bold, c.yellow, prompt))
            val = int(raw.strip())
            if lo <= val <= hi:
                return val
            print(clr(c.red, f"  Please enter a number between {lo} and {hi}."))
        except ValueError:
            print(clr(c.red, f"  '{raw.strip()}' is not a valid number. Try again."))
        except (EOFError, KeyboardInterrupt):
            _goodbye()

def prompt_int_or_default(prompt: str, lo: int, hi: int, default: int) -> int:
    """Like prompt_int, but pressing Enter (empty input) returns `default`."""
    while True:
        try:
            raw = input(clr(c.bold, c.yellow, prompt)).strip()
            if raw == "":
                return default
            val = int(raw)
            if lo <= val <= hi:
                return val
            print(clr(c.red,
                f"  Please enter a number between {lo} and {hi}, "
                f"or press Enter to use the default."))
        except ValueError:
            print(clr(c.red,
                f"  '{raw}' is not a valid number. "
                f"Enter a number between {lo} and {hi}, or press Enter for default."))
        except (EOFError, KeyboardInterrupt):
            _goodbye()

def prompt_float_positive(prompt: str) -> float:
    """Prompt for a positive float speed value, with detailed error messages."""
    while True:
        try:
            raw = input(clr(c.bold, c.yellow, prompt)).strip()
            if not raw:
                print(clr(c.red, "  Please enter a value, e.g. 1.3  (or press Ctrl+C to quit)"))
                continue
            val = float(raw)
            if val <= 0:
                print(clr(c.red, "  Speed must be greater than 0."))
            elif val > 100:
                print(clr(c.red, "  Speed seems unreasonably high (max allowed: 100). Try again."))
            else:
                return val
        except ValueError:
            print(clr(c.red,
                f"  '{raw}' is not a valid number. "
                "Use a decimal like 1.3 or 0.8."))
        except (EOFError, KeyboardInterrupt):
            _goodbye()

# ── Config loading ────────────────────────────────────────────────────────────
def load_playlists(path: Path) -> dict[str, str]:
    # File existence / read errors
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise SystemExit(
            clr(c.red, f"Config file not found: {path}\n") +
            clr(c.dim, "Create it or pass a custom path as argument.")
        )
    except PermissionError:
        raise SystemExit(clr(c.red, f"Permission denied reading config: {path}"))
    except OSError as e:
        raise SystemExit(clr(c.red, f"Could not read config file: {e}"))

    # Empty file
    if not text.strip():
        raise SystemExit(clr(c.red, f"Config file is empty: {path}"))

    # JSON parse errors
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise SystemExit(clr(c.red, f"JSON parse error in {path}:\n  {e}"))

    # Shape validation
    if not isinstance(data, dict):
        raise SystemExit(clr(c.red,
            f"Invalid format: expected a JSON object, got {type(data).__name__}."))

    bad_types = [k for k, v in data.items()
                 if not isinstance(k, str) or not isinstance(v, str)]
    if bad_types:
        raise SystemExit(clr(c.red,
            "Invalid format: all keys and values must be strings.\n"
            f"  Offending keys: {', '.join(repr(k) for k in bad_types)}"))

    # Blank name or URL
    invalid = [k for k, v in data.items() if not k.strip() or not v.strip()]
    if invalid:
        raise SystemExit(clr(c.red,
            f"Playlist entries with empty name or URL: "
            f"{', '.join(repr(k) for k in invalid)}"))

    return data

# ── Main (one-shot, then exec into mpv) ───────────────────────────────────────
def main():
    # ── Config path resolution ────────────────────────────────────────────────
    if len(sys.argv) > 1:
        config_path = Path(sys.argv[1])
    else:
        # Resolve relative to the script/binary's own directory, not CWD.
        # Under PyInstaller sys.frozen is set and sys.executable points to the
        # actual binary; __file__ would resolve to the temp extraction dir.
        if getattr(sys, "frozen", False):
            base_path = Path(sys.executable).parent
        else:
            base_path = Path(__file__).parent
        config_path = base_path / "playlists.json"

    # ── Load playlists ────────────────────────────────────────────────────────
    playlists = load_playlists(config_path)

    if not playlists:
        raise SystemExit(clr(c.red, "No playlists found in config."))

    # ── Playlist menu ─────────────────────────────────────────────────────────
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
        _goodbye()

    selected_name = names[choice - 1]
    url = playlists[selected_name]

    # ── Speed selection ───────────────────────────────────────────────────────
    PRESET_SPEEDS = [0.75, 0.80, 0.85, 0.90, 0.95, 1.00, 1.25, 1.50, 1.75, 2.00]
    DEFAULT_SPEED_IDX = PRESET_SPEEDS.index(1.00) + 1  # 1-based index → 6
    custom_idx = len(PRESET_SPEEDS) + 1

    print()
    print(clr(c.bold, c.white, "  Playback Speed"))
    print(clr(c.dim,           "  ─────────────────────────────────────"))
    for i, spd in enumerate(PRESET_SPEEDS, 1):
        marker = clr(c.bold, c.green, " ◀ default (Enter)") if spd == 1.00 else ""
        print(f"  {clr(c.bold, c.cyan, f'[{i}]')}  {clr(c.white, f'{spd:.2f}x')}{marker}")
    print(f"  {clr(c.bold, c.yellow, f'[{custom_idx}]')}  Custom speed\n")

    speed_choice = prompt_int_or_default(
        "  Select a speed (Enter = 1.00x): ",
        lo=1, hi=custom_idx, default=DEFAULT_SPEED_IDX,
    )

    if speed_choice == custom_idx:
        playback_speed = prompt_float_positive("  Enter custom speed (e.g. 1.3): ")
    else:
        playback_speed = PRESET_SPEEDS[speed_choice - 1]

    ok(f"Speed set to {playback_speed}x")

    # ── Build mpv args and launch ─────────────────────────────────────────────
    mpv_args = [
        f"--speed={playback_speed}",
        "--ytdl-format=bestaudio",
        # "--no-video",   # uncomment to force audio-only mode
        "--shuffle",
        url,
    ]

    print(clr(c.green, c.bold, f"\nLaunching {selected_name}..."))
    print(clr(c.dim, "(The launcher will be replaced by mpv — press 'q' to quit mpv)\n"))

    # Replace the current process with mpv (no launcher left in memory)
    try:
        os.execvp("mpv", ["mpv"] + mpv_args)
    except FileNotFoundError:
        err("mpv not found. Install it with your package manager "
            "(e.g. 'sudo apt install mpv' or 'brew install mpv').")
        sys.exit(1)
    except PermissionError:
        err("Permission denied when trying to run mpv. Check file permissions.")
        sys.exit(1)
    except OSError as e:
        err(f"Failed to launch mpv: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
