#!/usr/bin/env python3
"""
jukempv — YouTube playlist quick-launcher for mpv.

Shows an interactive terminal menu (playlist, then playback speed) and hands
control over to mpv.  On POSIX the launcher process is *replaced* by mpv via
os.execv, so nothing lingers in memory.  Windows has no true process
replacement, so mpv runs as a child process there and its exit code is
propagated.

Usage:
    python jukempv.py [options] [path/to/playlists.json]

Options:
    --no-video     Never open a video window (audio only).
    --no-shuffle   Play playlists in their original order.

Config format (playlists.json):
    {
        "Lo-Fi Chill": "https://www.youtube.com/playlist?list=...",
        "Deep Focus":  "https://www.youtube.com/playlist?list=..."
    }

Entries can be added automatically with add-to-playlist.py.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NoReturn
from urllib.parse import urlparse

# ── Constants ─────────────────────────────────────────────────────────────────

CONFIG_FILENAME = "playlists.json"

MIN_SPEED = 0.01    # mpv's documented lower bound for --speed
MAX_SPEED = 100.0   # mpv's documented upper bound for --speed

_PRESET_SPEEDS: tuple[float, ...] = (
    0.75, 0.80, 0.85, 0.90, 0.95,
    1.00,
    1.25, 1.50, 1.75, 2.00,
)
_DEFAULT_SPEED: float = 1.00

_RULE_WIDTH = 37


# ── Output encoding & ANSI colour support ─────────────────────────────────────

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


class _Color:
    """Whether colour is enabled for stdout / stderr (decided once at start-up)."""
    out = False
    err = False


def _enable_windows_vt(std_handle_id: int) -> bool:
    """
    Turn on ANSI/VT processing for one Windows console handle.

    The *current* console mode is read and the VT flag OR-ed in, so existing
    flags are preserved.  Returns False if the handle is not a console.
    """
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel32.GetStdHandle.restype = wintypes.HANDLE
        kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetConsoleMode.restype = wintypes.BOOL
        kernel32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.SetConsoleMode.restype = wintypes.BOOL

        handle = kernel32.GetStdHandle(std_handle_id & 0xFFFFFFFF)
        mode = wintypes.DWORD()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        enable_virtual_terminal_processing = 0x0004
        return bool(kernel32.SetConsoleMode(handle, mode.value | enable_virtual_terminal_processing))
    except Exception:
        return False


def _supports_color(stream, std_handle_id: int) -> bool:
    """Colour only on real terminals, and honour the NO_COLOR convention."""
    if os.environ.get("NO_COLOR"):
        return False
    if stream is None or not stream.isatty():
        return False
    if sys.platform == "win32":
        return _enable_windows_vt(std_handle_id)
    return os.environ.get("TERM", "") != "dumb"


def _init_terminal() -> None:
    """
    Make console output robust:

    * never crash on characters the console encoding can't represent
      (e.g. emoji when output is piped through a legacy Windows code page);
    * enable colour only when it will actually render.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(errors="replace")
            except (OSError, ValueError):
                pass

    _Color.out = _supports_color(sys.stdout, -11)   # STD_OUTPUT_HANDLE
    _Color.err = _supports_color(sys.stderr, -12)   # STD_ERROR_HANDLE


def styled(*codes: str, text: str, err: bool = False) -> str:
    """Wrap *text* with ANSI *codes* (when colour is enabled) and reset."""
    enabled = _Color.err if err else _Color.out
    if not enabled:
        return text
    return "".join(codes) + text + Ansi.RESET


# ── Terminal helpers ──────────────────────────────────────────────────────────

def clear_screen() -> None:
    """Clear the terminal (only when attached to one)."""
    if not sys.stdout.isatty():
        return
    if _Color.out:
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.flush()
    elif sys.platform == "win32":
        os.system("cls")


def print_header() -> None:
    border = styled(Ansi.BOLD, Ansi.BLUE, text="════════════════════")
    title  = styled(Ansi.BOLD, Ansi.BLUE, text="   🎵  JukeMPV  🎵   ")
    print(f"{border}\n{title}\n{border}\n")


def print_ok(message: str) -> None:
    print(styled(Ansi.GREEN, Ansi.BOLD, text="[✓] ") + message)


def print_warning(message: str) -> None:
    prefix = styled(Ansi.YELLOW, Ansi.BOLD, text="[!] ", err=True)
    print(prefix + message, file=sys.stderr)


def print_error(message: str) -> None:
    prefix = styled(Ansi.RED, Ansi.BOLD, text="[error] ", err=True)
    body   = styled(Ansi.RED, text=message, err=True)
    print(prefix + body, file=sys.stderr)


def print_hint(message: str) -> None:
    """Inline validation feedback shown beneath a prompt."""
    print(styled(Ansi.RED, text=f"  {message}"))


def print_section(title: str) -> None:
    heading   = styled(Ansi.BOLD, Ansi.WHITE, text=f"  {title}")
    separator = styled(Ansi.DIM, text="  " + "─" * _RULE_WIDTH)
    print(heading)
    print(separator)


def die(message: str) -> NoReturn:
    """Report a fatal error and exit with status 1."""
    print_error(message)
    sys.exit(1)


def goodbye() -> NoReturn:
    print(styled(Ansi.DIM, text="\nGoodbye!\n"))
    sys.exit(0)


# ── Input helpers ─────────────────────────────────────────────────────────────

def _read_line(prompt: str) -> str:
    """input() wrapper: styled prompt, stripped result, clean exit on EOF/Ctrl-C."""
    try:
        return input(styled(Ansi.BOLD, Ansi.YELLOW, text=prompt)).strip()
    except (EOFError, KeyboardInterrupt):
        goodbye()


def prompt_int(prompt: str, lo: int, hi: int, default: int | None = None) -> int:
    """
    Prompt for an integer in the closed interval [lo, hi].

    If *default* is given, pressing Enter alone returns it.  Loops until the
    input is valid; exits cleanly on EOF / Ctrl-C.
    """
    enter_hint = ", or press Enter for the default" if default is not None else ""
    while True:
        raw = _read_line(prompt)
        if not raw and default is not None:
            return default
        try:
            value = int(raw)
        except ValueError:
            shown = f"'{raw}'" if raw else "Empty input"
            print_hint(f"{shown} is not a valid number. Enter {lo}–{hi}{enter_hint}.")
            continue
        if lo <= value <= hi:
            return value
        print_hint(f"Enter a number between {lo} and {hi}{enter_hint}.")


def prompt_speed(prompt: str) -> float:
    """
    Prompt for a custom playback speed within mpv's supported range.

    Accepts '.' or ',' as the decimal separator and rejects NaN / infinity
    (which float() happily parses and every range comparison lets through).
    """
    while True:
        raw = _read_line(prompt)
        if not raw:
            print_hint("Please enter a value, e.g. 1.3  (Ctrl+C to quit).")
            continue
        try:
            value = float(raw.replace(",", "."))
        except ValueError:
            print_hint(f"'{raw}' is not a valid number. Use a decimal like 1.3 or 0.8.")
            continue
        if not math.isfinite(value) or not (MIN_SPEED <= value <= MAX_SPEED):
            print_hint(f"Speed must be between {MIN_SPEED:g} and {MAX_SPEED:g}.")
            continue
        return value


# ── Config loading ────────────────────────────────────────────────────────────

class ConfigError(Exception):
    """Raised when the playlist config is missing, unreadable, or invalid."""


def resolve_config_path(cli_path: str | None) -> Path:
    """
    Return the config path: the CLI argument if given, otherwise
    playlists.json next to the script (or next to the executable when frozen
    with PyInstaller, where __file__ points into a temporary directory).
    """
    if cli_path:
        return Path(cli_path).expanduser()

    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path(__file__).resolve().parent
    return base / CONFIG_FILENAME


def _is_valid_url(url: str) -> bool:
    """True if *url* is an absolute http(s) URL with a host."""
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def load_playlists(path: Path) -> dict[str, str]:
    """
    Load and validate the JSON playlist config.

    Expected format: a flat JSON object mapping playlist names to URLs.
    Raises ConfigError with a descriptive message on any problem.
    """
    try:
        # utf-8-sig transparently strips the BOM that Windows Notepad adds.
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise ConfigError(
            f"Config file not found: {path}\n"
            f"Create it, add entries with add-to-playlist.py, "
            f"or pass a custom path as an argument."
        ) from None
    except PermissionError:
        raise ConfigError(f"Permission denied reading config: {path}") from None
    except UnicodeDecodeError:
        raise ConfigError(f"Config file is not valid UTF-8: {path}") from None
    except OSError as exc:
        raise ConfigError(f"Could not read config file: {exc}") from None

    if not text.strip():
        raise ConfigError(f"Config file is empty: {path}")

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"JSON parse error in {path}:\n  {exc}") from None

    if not isinstance(data, dict):
        raise ConfigError(
            f"Invalid format: expected a JSON object, got {type(data).__name__}."
        )
    if not data:
        raise ConfigError(f"No playlists found in {path}.")

    problems: list[str] = []
    for name, url in data.items():
        if not name.strip():
            problems.append(f"{name!r}: playlist name is blank")
        elif not isinstance(url, str) or not url.strip():
            problems.append(f"{name!r}: URL must be a non-empty string")
        elif not _is_valid_url(url):
            problems.append(f"{name!r}: not a valid http(s) URL")
    if problems:
        raise ConfigError("Invalid playlist entries:\n  " + "\n  ".join(problems))

    return {name: url.strip() for name, url in data.items()}


# ── mpv launcher ──────────────────────────────────────────────────────────────

def _find_ytdl(mpv_path: str) -> bool:
    """Look for yt-dlp / youtube-dl on PATH or beside the mpv binary."""
    search_dirs = (None, os.path.dirname(mpv_path))
    return any(
        shutil.which(name, path=directory)
        for name in ("yt-dlp", "youtube-dl")
        for directory in search_dirs
    )


def build_mpv_args(url: str, speed: float, *, shuffle: bool, no_video: bool) -> list[str]:
    """Return the mpv argument list (without the executable name)."""
    args = [
        f"--speed={speed:g}",
        "--ytdl-format=bestaudio",
    ]
    if shuffle:
        args.append("--shuffle")
    if no_video:
        args.append("--no-video")
    args.append(url)
    return args


def launch_mpv(
    url: str,
    speed: float,
    label: str,
    *,
    shuffle: bool = True,
    no_video: bool = False,
) -> NoReturn:
    """
    Hand execution over to mpv.

    POSIX: os.execv replaces this process, so no launcher lingers in memory.
    Windows: execv is emulated by the C runtime (it spawns a new process and
    exits, leaving the console detached from the child), so mpv is run with
    subprocess instead and its exit code is propagated.
    """
    mpv_path = shutil.which("mpv")
    if mpv_path is None:
        die(
            "mpv not found on PATH. Install it with your package manager:\n"
            "  • Linux:   sudo apt install mpv\n"
            "  • macOS:   brew install mpv\n"
            "  • Windows: https://mpv.io/installation/"
        )

    if not _find_ytdl(mpv_path):
        print_warning(
            "yt-dlp was not found on PATH or next to mpv; "
            "YouTube playback may fail. See https://github.com/yt-dlp/yt-dlp"
        )

    command = ["mpv", *build_mpv_args(url, speed, shuffle=shuffle, no_video=no_video)]
    on_windows = sys.platform == "win32"

    print(styled(Ansi.GREEN, Ansi.BOLD, text=f"\nLaunching '{label}'…"))
    handoff = "mpv will run in this window" if on_windows else "The launcher will be replaced by mpv"
    print(styled(Ansi.DIM, text=f"({handoff} — press 'q' to quit mpv)\n"))

    # Anything still buffered would be lost when the process image is replaced.
    sys.stdout.flush()
    sys.stderr.flush()

    try:
        if on_windows:
            try:
                returncode = subprocess.run([mpv_path, *command[1:]], check=False).returncode
            except KeyboardInterrupt:
                returncode = 130
            sys.exit(returncode)
        os.execv(mpv_path, command)
    except PermissionError:
        die("Permission denied when trying to run mpv. Check file permissions.")
    except OSError as exc:
        die(f"Failed to launch mpv: {exc}")


# ── Interactive menus ─────────────────────────────────────────────────────────

def select_playlist(playlists: dict[str, str]) -> tuple[str, str]:
    """Display the playlist menu and return (name, url) for the selection."""
    names = list(playlists)

    print_section("Your Playlists")
    for i, name in enumerate(names, start=1):
        idx_label = styled(Ansi.BOLD, Ansi.CYAN, text=f"[{i}]")
        name_text = styled(Ansi.WHITE, text=name)
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

    # 1-based index of the default speed; fall back to the first entry.
    try:
        default_idx = _PRESET_SPEEDS.index(_DEFAULT_SPEED) + 1
    except ValueError:
        default_idx = 1

    print()
    print_section("Playback Speed")
    for i, speed in enumerate(_PRESET_SPEEDS, start=1):
        idx_label = styled(Ansi.BOLD, Ansi.CYAN, text=f"[{i}]")
        spd_text  = styled(Ansi.WHITE, text=f"{speed:.2f}x")
        default_marker = (
            styled(Ansi.BOLD, Ansi.GREEN, text=" ◀ default (Enter)")
            if speed == _DEFAULT_SPEED else ""
        )
        print(f"  {idx_label}  {spd_text}{default_marker}")

    custom_label = styled(Ansi.BOLD, Ansi.YELLOW, text=f"[{custom_idx}]")
    print(f"  {custom_label}  Custom speed\n")

    choice = prompt_int(
        f"  Select a speed (Enter = {_DEFAULT_SPEED:.2f}x): ",
        lo=1,
        hi=custom_idx,
        default=default_idx,
    )

    if choice == custom_idx:
        return prompt_speed("  Enter custom speed (e.g. 1.3): ")

    return _PRESET_SPEEDS[choice - 1]


# ── Entry point ───────────────────────────────────────────────────────────────

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="jukempv",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "config",
        nargs="?",
        default=None,
        help=f"path to the playlist config (default: {CONFIG_FILENAME} next to this script)",
    )
    parser.add_argument("--no-video", action="store_true", help="never open a video window")
    parser.add_argument("--no-shuffle", action="store_true", help="play in original order")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    _init_terminal()
    args = parse_args(argv)

    try:
        playlists = load_playlists(resolve_config_path(args.config))
    except ConfigError as exc:
        die(str(exc))

    clear_screen()
    print_header()

    playlist_name, url = select_playlist(playlists)
    speed = select_speed()

    print_ok(f"Speed set to {speed:.2f}x")

    launch_mpv(
        url=url,
        speed=speed,
        label=playlist_name,
        shuffle=not args.no_shuffle,
        no_video=args.no_video,
    )


if __name__ == "__main__":
    sys.exit(main())