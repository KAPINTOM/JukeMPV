#!/usr/bin/env python3
"""
jukempv — YouTube playlist quick-launcher for mpv.

Presents an interactive terminal menu, then replaces itself with mpv
(via os.execvp on POSIX) so no launcher process remains in memory.
On Windows, subprocess.run is used as a fallback since execvp is
not a true process-replacement there.

Usage:
    python jukempv.py [path/to/playlists.json]

Config format (playlists.json):
    {
        "Lo-Fi Chill":    "https://www.youtube.com/playlist?list=...",
        "Deep Focus":     "https://www.youtube.com/playlist?list=..."
    }
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


# ── ANSI escape codes ─────────────────────────────────────────────────────────

class Ansi:
    """Terminal colour/style escape sequences."""
    RESET  = "\033[0m"
    BOLD   = "\033[1m"
    DIM    = "\033[2m"
    RED    = "\033[31m"
    GREEN  = "\033[32m"
    YELLOW = "\033[33m"
    BLUE   = "\033[34m"
    CYAN   = "\033[36m"
    WHITE  = "\033[97m"


def _enable_ansi_on_windows() -> None:
    """Enable ANSI VT processing in Windows cmd / PowerShell (non-fatal)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    except Exception:
        pass


_enable_ansi_on_windows()


def styled(*codes: str, text: str) -> str:
    """Wrap *text* with ANSI *codes* and append a reset."""
    return "".join(codes) + text + Ansi.RESET


# ── Terminal helpers ──────────────────────────────────────────────────────────

def clear_screen() -> None:
    """Clear the terminal in a cross-platform way."""
    if sys.platform == "win32":
        os.system("cls")
    else:
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.flush()


def print_header() -> None:
    border = styled(Ansi.BOLD, Ansi.BLUE, text="════════════════════")
    title  = styled(Ansi.BOLD, Ansi.BLUE, text="   🎵  JukeMPV  🎵   ")
    print(f"{border}\n{title}\n{border}\n")


def print_ok(message: str) -> None:
    prefix = styled(Ansi.GREEN, Ansi.BOLD, text="[✓] ")
    print(prefix + message)


def print_info(message: str) -> None:
    prefix = styled(Ansi.CYAN, Ansi.BOLD, text="[i] ")
    print(prefix + message)


def print_error(message: str) -> None:
    prefix = styled(Ansi.RED, Ansi.BOLD, text="[error] ")
    body   = styled(Ansi.RED, text=message)
    print(prefix + body, file=sys.stderr)


def print_section(title: str) -> None:
    heading   = styled(Ansi.BOLD, Ansi.WHITE, text=f"  {title}")
    separator = styled(Ansi.DIM,              text="  " + "─" * 37)
    print(heading)
    print(separator)


def goodbye() -> None:
    print(styled(Ansi.DIM, text="\nGoodbye!\n"))
    sys.exit(0)


# ── Input helpers ─────────────────────────────────────────────────────────────

def prompt_int(prompt: str, lo: int, hi: int) -> int:
    """
    Prompt for an integer in the closed interval [lo, hi].
    Loops indefinitely on invalid input; exits cleanly on EOF/Ctrl-C.
    """
    formatted_prompt = styled(Ansi.BOLD, Ansi.YELLOW, text=prompt)
    while True:
        try:
            raw = input(formatted_prompt).strip()
            value = int(raw)
            if lo <= value <= hi:
                return value
            print(styled(Ansi.RED, text=f"  Enter a number between {lo} and {hi}."))
        except ValueError:
            print(styled(Ansi.RED, text=f"  '{raw}' is not a valid number. Try again."))
        except (EOFError, KeyboardInterrupt):
            goodbye()


def prompt_int_or_default(prompt: str, lo: int, hi: int, default: int) -> int:
    """
    Like prompt_int, but pressing Enter alone returns *default*.
    """
    formatted_prompt = styled(Ansi.BOLD, Ansi.YELLOW, text=prompt)
    while True:
        try:
            raw = input(formatted_prompt).strip()
            if raw == "":
                return default
            value = int(raw)
            if lo <= value <= hi:
                return value
            print(styled(Ansi.RED,
                text=f"  Enter a number between {lo} and {hi}, "
                     f"or press Enter for the default."))
        except ValueError:
            print(styled(Ansi.RED,
                text=f"  '{raw}' is not a valid number. "
                     f"Enter {lo}–{hi}, or press Enter for the default."))
        except (EOFError, KeyboardInterrupt):
            goodbye()


def prompt_positive_float(prompt: str, max_value: float = 100.0) -> float:
    """
    Prompt for a positive float (e.g. a playback speed).
    Rejects non-positive values and values exceeding *max_value*.
    """
    formatted_prompt = styled(Ansi.BOLD, Ansi.YELLOW, text=prompt)
    while True:
        try:
            raw = input(formatted_prompt).strip()
            if not raw:
                print(styled(Ansi.RED,
                    text="  Please enter a value, e.g. 1.3  (Ctrl+C to quit)."))
                continue
            value = float(raw)
            if value <= 0:
                print(styled(Ansi.RED, text="  Speed must be greater than 0."))
            elif value > max_value:
                print(styled(Ansi.RED,
                    text=f"  Speed is unreasonably high (max: {max_value}). Try again."))
            else:
                return value
        except ValueError:
            print(styled(Ansi.RED,
                text=f"  '{raw}' is not a valid number. Use a decimal like 1.3 or 0.8."))
        except (EOFError, KeyboardInterrupt):
            goodbye()


# ── Config loading ────────────────────────────────────────────────────────────

def _resolve_config_path(argv: list[str]) -> Path:
    """
    Return the config file path from CLI args, or default to
    playlists.json next to the script/binary.

    Under PyInstaller, sys.executable points to the bundled binary,
    so we use that instead of __file__ (which resolves to a temp dir).
    """
    if len(argv) > 1:
        return Path(argv[1])

    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).parent

    return base / "playlists.json"


def _validate_url(url: str) -> bool:
    """Return True if *url* looks like an http(s) URL (basic sanity check)."""
    return url.startswith(("http://", "https://"))


def load_playlists(path: Path) -> dict[str, str]:
    """
    Load and validate the JSON playlist config from *path*.

    Expected format: a flat JSON object mapping playlist names to URLs.
    Raises SystemExit with a descriptive message on any error.
    """
    # ── Read file ─────────────────────────────────────────────────────────────
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise SystemExit(
            styled(Ansi.RED, text=f"Config file not found: {path}\n") +
            styled(Ansi.DIM, text="Create it or pass a custom path as an argument.")
        )
    except PermissionError:
        raise SystemExit(styled(Ansi.RED, text=f"Permission denied reading config: {path}"))
    except OSError as exc:
        raise SystemExit(styled(Ansi.RED, text=f"Could not read config file: {exc}"))

    if not text.strip():
        raise SystemExit(styled(Ansi.RED, text=f"Config file is empty: {path}"))

    # ── Parse JSON ────────────────────────────────────────────────────────────
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(styled(Ansi.RED, text=f"JSON parse error in {path}:\n  {exc}"))

    # ── Shape validation ──────────────────────────────────────────────────────
    if not isinstance(data, dict):
        raise SystemExit(styled(Ansi.RED,
            text=f"Invalid format: expected a JSON object, got {type(data).__name__}."))

    wrong_types = [
        k for k, v in data.items()
        if not isinstance(k, str) or not isinstance(v, str)
    ]
    if wrong_types:
        keys = ", ".join(repr(k) for k in wrong_types)
        raise SystemExit(styled(Ansi.RED,
            text=f"Invalid format: all keys and values must be strings.\n"
                 f"  Offending keys: {keys}"))

    blank_entries = [k for k, v in data.items() if not k.strip() or not v.strip()]
    if blank_entries:
        keys = ", ".join(repr(k) for k in blank_entries)
        raise SystemExit(styled(Ansi.RED,
            text=f"Playlist entries with blank name or URL: {keys}"))

    invalid_urls = [k for k, v in data.items() if not _validate_url(v)]
    if invalid_urls:
        keys = ", ".join(repr(k) for k in invalid_urls)
        raise SystemExit(styled(Ansi.RED,
            text=f"Playlist entries with invalid URLs (must start with http/https): {keys}"))

    return data


# ── mpv launcher ──────────────────────────────────────────────────────────────

def launch_mpv(url: str, speed: float, label: str) -> None:
    """
    Build the mpv argument list and hand off execution to mpv.

    On POSIX, os.execvp replaces the current process entirely so that no
    launcher process lingers in memory.  On Windows, execvp is emulated by
    the C runtime and does *not* free the Python process, so we fall back to
    subprocess.run and exit cleanly afterwards.
    """
    mpv_args: list[str] = [
        f"--speed={speed}",
        "--ytdl-format=bestaudio",
        # "--no-video",   # uncomment to force audio-only
        "--shuffle",
        url,
    ]

    print(styled(Ansi.GREEN, Ansi.BOLD, text=f"\nLaunching '{label}'…"))
    print(styled(Ansi.DIM,
        text="(The launcher will be replaced by mpv — press 'q' to quit mpv)\n"))

    try:
        if sys.platform == "win32":
            result = subprocess.run(["mpv"] + mpv_args, check=False)
            sys.exit(result.returncode)
        else:
            os.execvp("mpv", ["mpv"] + mpv_args)
    except FileNotFoundError:
        print_error(
            "mpv not found. Install it with your package manager:\n"
            "  • Linux:  sudo apt install mpv\n"
            "  • macOS:  brew install mpv\n"
            "  • Windows: https://mpv.io/installation/"
        )
        sys.exit(1)
    except PermissionError:
        print_error("Permission denied when trying to run mpv. Check file permissions.")
        sys.exit(1)
    except OSError as exc:
        print_error(f"Failed to launch mpv: {exc}")
        sys.exit(1)


# ── Interactive menus ─────────────────────────────────────────────────────────

_PRESET_SPEEDS: tuple[float, ...] = (
    0.75, 0.80, 0.85, 0.90, 0.95,
    1.00,
    1.25, 1.50, 1.75, 2.00,
)
_DEFAULT_SPEED: float = 1.00


def select_playlist(playlists: dict[str, str]) -> tuple[str, str]:
    """Display the playlist menu and return (name, url) for the selection."""
    names = list(playlists.keys())

    print_section("Your Playlists")
    for i, name in enumerate(names, start=1):
        idx_label = styled(Ansi.BOLD, Ansi.CYAN,  text=f"[{i}]")
        name_text = styled(Ansi.WHITE,             text=name)
        print(f"  {idx_label}  {name_text}")

    exit_label = styled(Ansi.BOLD, Ansi.RED, text="[0]")
    print(f"  {exit_label}  Exit\n")

    choice = prompt_int("  Select a playlist: ", lo=0, hi=len(names))
    if choice == 0:
        goodbye()

    selected = names[choice - 1]
    return selected, playlists[selected]


def select_speed() -> float:
    """Display the speed menu and return the chosen playback speed."""
    custom_idx = len(_PRESET_SPEEDS) + 1

    # Locate the default speed index (1-based); fall back to 1 if not found.
    try:
        default_idx = _PRESET_SPEEDS.index(_DEFAULT_SPEED) + 1
    except ValueError:
        default_idx = 1

    print()
    print_section("Playback Speed")
    for i, speed in enumerate(_PRESET_SPEEDS, start=1):
        idx_label = styled(Ansi.BOLD, Ansi.CYAN, text=f"[{i}]")
        spd_text  = styled(Ansi.WHITE,            text=f"{speed:.2f}x")
        default_marker = (
            styled(Ansi.BOLD, Ansi.GREEN, text=" ◀ default (Enter)")
            if speed == _DEFAULT_SPEED else ""
        )
        print(f"  {idx_label}  {spd_text}{default_marker}")

    custom_label = styled(Ansi.BOLD, Ansi.YELLOW, text=f"[{custom_idx}]")
    print(f"  {custom_label}  Custom speed\n")

    choice = prompt_int_or_default(
        f"  Select a speed (Enter = {_DEFAULT_SPEED:.2f}x): ",
        lo=1,
        hi=custom_idx,
        default=default_idx,
    )

    if choice == custom_idx:
        return prompt_positive_float("  Enter custom speed (e.g. 1.3): ")

    return _PRESET_SPEEDS[choice - 1]


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    config_path = _resolve_config_path(sys.argv)
    playlists   = load_playlists(config_path)

    if not playlists:
        raise SystemExit(styled(Ansi.RED, text="No playlists found in config."))

    clear_screen()
    print_header()

    playlist_name, url = select_playlist(playlists)
    speed              = select_speed()

    print_ok(f"Speed set to {speed:.2f}x")

    launch_mpv(url=url, speed=speed, label=playlist_name)


if __name__ == "__main__":
    main()