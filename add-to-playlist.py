#!/usr/bin/env python3
"""
add-to-playlist.py
==================
Add a YouTube video or playlist URL to a local ``playlists.json`` catalogue.
Supports regular YouTube links, ``youtu.be`` short-links, and
``music.youtube.com`` links (automatically rewritten to ``www.youtube.com``).

Usage
-----
Interactive (prompts for URL):
    python add-to-playlist.py

Non-interactive (pass URL directly):
    python add-to-playlist.py <url>

Optional flags:
    --dry-run   Resolve the title but do not write to disk.
    --verbose   Print debug-level information.
    --json <path>  Override the default playlists.json path.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
from urllib.parse import ParseResult, parse_qs, urlencode, urlparse, urlunparse

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCRIPT_DIR: str = os.path.dirname(os.path.abspath(__file__))
DEFAULT_JSON_PATH: str = os.path.join(SCRIPT_DIR, "playlists.json")

YOUTUBE_HOSTS: frozenset[str] = frozenset(
    {"www.youtube.com", "youtube.com", "youtu.be", "m.youtube.com", "music.youtube.com"}
)

# Tracking / noise query parameters that should be stripped from stored URLs.
_STRIP_PARAMS: frozenset[str] = frozenset({"si", "pp", "feature", "ab_channel"})

_REQUEST_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

_NETWORK_TIMEOUT: int = 15          # seconds per request attempt
_MAX_RETRIES: int = 3               # total attempts before giving up
_RETRY_BACKOFF: float = 1.5        # seconds; doubles on each retry

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

log = logging.getLogger("add-to-playlist")


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    log.setLevel(level)
    log.addHandler(handler)


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def is_youtube_url(url: str) -> bool:
    """Return *True* only for recognised YouTube hostnames."""
    try:
        return urlparse(url).netloc.lower() in YOUTUBE_HOSTS
    except Exception:
        return False


def normalize_url(url: str) -> str:
    """
    Canonicalize a YouTube URL so that semantically identical links are stored
    as a single entry.

    Steps applied:
      1. Strip tracking / noise query parameters (``si``, ``pp``, …).
      2. Convert ``youtu.be/<id>`` short-links to the canonical watch URL.
      3. Rewrite ``music.youtube.com`` links to ``www.youtube.com``.
      4. Remove any URL fragment (``#t=30``).
    """
    parsed: ParseResult = urlparse(url)
    host = parsed.netloc.lower()

    # Expand youtu.be/<video-id> → youtube.com/watch?v=<video-id>
    if host == "youtu.be":
        video_id = parsed.path.lstrip("/")
        query = parse_qs(parsed.query, keep_blank_values=True)
        query["v"] = [video_id]
        query = {k: v for k, v in query.items() if k not in _STRIP_PARAMS}
        new_query = urlencode(query, doseq=True)
        parsed = ParseResult(
            scheme="https",
            netloc="www.youtube.com",
            path="/watch",
            params="",
            query=new_query,
            fragment="",
        )
        log.debug("Expanded short-link → %s", parsed.geturl())
        return parsed.geturl()

    # Rewrite music.youtube.com → www.youtube.com so the stored URL is always
    # the standard YouTube domain, regardless of which surface the user copied
    # the link from.
    if host == "music.youtube.com":
        log.debug("Rewriting music.youtube.com → www.youtube.com")
        parsed = parsed._replace(
            scheme="https",
            netloc="www.youtube.com",
        )

    # Strip noise parameters and fragment from regular URLs.
    query_dict = {
        k: v
        for k, v in parse_qs(parsed.query, keep_blank_values=True).items()
        if k not in _STRIP_PARAMS
    }
    stripped = parse_qs(parsed.query, keep_blank_values=True).keys() - query_dict.keys()
    if stripped:
        log.debug("Stripped query parameters: %s", ", ".join(sorted(stripped)))

    clean_query = urlencode(query_dict, doseq=True) if query_dict else ""
    return urlunparse(parsed._replace(query=clean_query, fragment=""))


# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------

def fetch_html(url: str) -> str | None:
    """
    Fetch the raw HTML of *url* with browser-like headers.

    Retries up to ``_MAX_RETRIES`` times with exponential back-off for
    transient errors (network timeouts, 5xx responses).  Permanent client
    errors (4xx except 429) are not retried.
    """
    req = urllib.request.Request(url, headers=_REQUEST_HEADERS)
    delay = _RETRY_BACKOFF

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=_NETWORK_TIMEOUT) as resp:
                html = resp.read().decode("utf-8")
                if not html:
                    log.warning("Response body was empty (attempt %d).", attempt)
                    return None
                log.debug("Fetched %d bytes from %s.", len(html), url)
                return html

        except urllib.error.HTTPError as exc:
            log.debug("HTTP %d %s (attempt %d).", exc.code, exc.reason, attempt)
            # Rate-limited or server-side error → retry; anything else → abort.
            if exc.code in {429, 500, 502, 503, 504} and attempt < _MAX_RETRIES:
                log.warning("HTTP %d — retrying in %.1fs…", exc.code, delay)
                time.sleep(delay)
                delay *= 2
            else:
                log.error("HTTP error %d: %s", exc.code, exc.reason)
                return None

        except urllib.error.URLError as exc:
            log.debug("URLError: %s (attempt %d).", exc.reason, attempt)
            if attempt < _MAX_RETRIES:
                log.warning("Network error — retrying in %.1fs…", delay)
                time.sleep(delay)
                delay *= 2
            else:
                log.error("Network error: %s", exc.reason)
                return None

        except TimeoutError:
            log.debug("Request timed out (attempt %d).", attempt)
            if attempt < _MAX_RETRIES:
                log.warning("Timeout — retrying in %.1fs…", delay)
                time.sleep(delay)
                delay *= 2
            else:
                log.error("Request timed out after %d attempts.", _MAX_RETRIES)
                return None

    return None  # unreachable, but satisfies type checkers


# ---------------------------------------------------------------------------
# JSON-blob extraction
# ---------------------------------------------------------------------------

def _extract_json_blob(html: str, var_name: str) -> dict | None:
    """
    Extract a JavaScript object literal assigned to *var_name*.

    Example target::

        var ytInitialData = { … };

    The parser walks the string character-by-character tracking brace depth and
    string-literal boundaries (both ``"`` and ``'``), correctly handling escape
    sequences.  This avoids the truncation bug caused by naïve regex matching on
    the first ``};``.
    """
    marker = f"var {var_name} = {{"
    start = html.find(marker)
    if start == -1:
        log.debug("Marker '%s' not found in HTML.", marker)
        return None

    # Rewind to the opening ``{``.
    start += len(marker) - 1
    depth = 0
    in_string = False
    string_char = ""
    i = start

    while i < len(html):
        ch = html[i]

        if in_string:
            if ch == "\\" :
                i += 2          # skip the escaped character entirely
                continue
            if ch == string_char:
                in_string = False
        else:
            if ch in ('"', "'"):
                in_string = True
                string_char = ch
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    blob = html[start : i + 1]
                    try:
                        result = json.loads(blob)
                        log.debug("Parsed '%s' (%d bytes).", var_name, len(blob))
                        return result
                    except json.JSONDecodeError as exc:
                        log.debug("JSON parse error in '%s': %s", var_name, exc)
                        return None
        i += 1

    log.debug("No matching closing brace found for '%s'.", var_name)
    return None


# ---------------------------------------------------------------------------
# Title extraction
# ---------------------------------------------------------------------------

# Ordered sequence of (description, dotted-key-path) pairs tried against
# ytInitialData.  A dotted path of the form ``a.b.0.c`` resolves nested
# dicts/lists; an integer segment is treated as a list index.
_INITIAL_DATA_TITLE_PATHS: list[tuple[str, str]] = [
    ("video details",           "videoDetails.title"),
    ("playlist metadata",       "metadata.playlistMetadataRenderer.title"),
    ("playlist header",         (
        "contents"
        ".twoColumnBrowseResultsRenderer"
        ".tabs.0.tabRenderer.content"
        ".sectionListRenderer.contents.0"
        ".itemSectionRenderer.contents.0"
        ".playlistHeaderRenderer.title.runs.0.text"
    )),
    ("microformat",             "microformat.microformatDataRenderer.title"),
]


def _deep_get(data: dict | list, dotted_path: str) -> object | None:
    """
    Traverse nested dicts/lists using a dot-separated path.

    Integer path segments are used as list indices.  Returns *None* on any
    missing key, out-of-range index, or type mismatch.
    """
    current: object = data
    for segment in dotted_path.split("."):
        try:
            key: int | str = int(segment)
        except ValueError:
            key = segment
        try:
            current = current[key]  # type: ignore[index]
        except (KeyError, IndexError, TypeError):
            return None
    return current


def get_youtube_title(url: str) -> str | None:
    """
    Resolve the human-readable title of a YouTube video or playlist.

    Extraction strategy (in order):
      1. ``ytInitialPlayerResponse.videoDetails.title`` — video pages only.
      2. Several paths inside ``ytInitialData`` — covers both videos and playlists.
    """
    html = fetch_html(url)
    if not html:
        return None

    # --- Strategy 1: ytInitialPlayerResponse (video pages) ---
    player_resp = _extract_json_blob(html, "ytInitialPlayerResponse")
    if player_resp:
        title = _deep_get(player_resp, "videoDetails.title")
        if isinstance(title, str) and title:
            log.debug("Title from ytInitialPlayerResponse: %r", title)
            return title

    # --- Strategy 2: ytInitialData (videos + playlists) ---
    initial_data = _extract_json_blob(html, "ytInitialData")
    if initial_data:
        for description, path in _INITIAL_DATA_TITLE_PATHS:
            title = _deep_get(initial_data, path)
            if isinstance(title, str) and title:
                log.debug("Title from ytInitialData[%s]: %r", description, title)
                return title

    log.warning("Could not extract title from page.")
    return None


# ---------------------------------------------------------------------------
# Catalogue (playlists.json) management
# ---------------------------------------------------------------------------

def load_catalogue(path: str) -> dict[str, str]:
    """
    Load the JSON catalogue from *path*.

    Returns an empty dict if the file does not exist.  On corruption, backs up
    the broken file with a ``.bak`` suffix before returning an empty dict so
    the user's data is preserved.
    """
    if not os.path.isfile(path):
        log.debug("Catalogue not found at %s — starting fresh.", path)
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError(f"Expected a JSON object, got {type(data).__name__}.")
        log.debug("Loaded %d entries from %s.", len(data), path)
        return data
    except (json.JSONDecodeError, ValueError) as exc:
        backup = path + ".bak"
        shutil.copy2(path, backup)
        log.error(
            "Catalogue is malformed (%s).  "
            "A backup was saved to %s.  Starting with an empty catalogue.",
            exc,
            backup,
        )
        return {}


def save_catalogue(path: str, data: dict[str, str]) -> None:
    """
    Persist *data* to *path* atomically.

    Writes to a sibling temporary file first and then replaces the target with
    ``os.replace`` (which is atomic on POSIX and best-effort on Windows).  This
    prevents corruption if the process is interrupted mid-write.
    """
    directory = os.path.dirname(path) or "."
    tmp_fd, tmp_path = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp_path, path)
        log.debug("Catalogue saved atomically to %s.", path)
    except Exception:
        # Clean up the orphaned temp file before re-raising.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def find_existing_entry(
    catalogue: dict[str, str], url: str
) -> tuple[str, str] | None:
    """
    Return the ``(title, stored_url)`` pair if *url* already appears in
    *catalogue*, or *None* otherwise.

    Comparison is performed on the normalized forms of both URLs so that
    equivalent links with different parameter ordering or tracking tokens are
    recognized as duplicates.
    """
    canonical = normalize_url(url)
    for title, stored_url in catalogue.items():
        if normalize_url(stored_url) == canonical:
            return title, stored_url
    return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Add a YouTube video or playlist URL to playlists.json."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "url",
        nargs="?",
        default=None,
        help="YouTube URL to add.  If omitted, the script prompts interactively.",
    )
    parser.add_argument(
        "--json",
        dest="json_path",
        default=DEFAULT_JSON_PATH,
        metavar="PATH",
        help="Path to the playlists.json file (default: %(default)s).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve the title but do not write to disk.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug-level logging.",
    )
    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(
    raw_url: str,
    json_path: str,
    *,
    dry_run: bool = False,
) -> int:
    """
    Core logic: validate → normalize → resolve title → deduplicate → persist.

    Returns 0 on success, 1 on any recoverable error.
    """
    # 1. Validate.
    if not is_youtube_url(raw_url):
        log.error("Not a recognised YouTube URL: %s", raw_url)
        return 1

    # 2. Normalize.
    url = normalize_url(raw_url)
    if url != raw_url:
        log.info("Normalized URL: %s", url)

    # 3. Resolve title.
    title = get_youtube_title(url)
    if not title:
        log.error("Could not retrieve title.  Check the URL and your connection.")
        return 1

    log.info("Title: %s", title)

    # 4. Load catalogue and check for duplicates.
    catalogue = load_catalogue(json_path)
    existing = find_existing_entry(catalogue, url)

    if existing is not None:
        existing_title, existing_url = existing
        if existing_title == title and existing_url == url:
            log.info("Entry already present — no changes made.")
            return 0
        # URL is the same but title or stored form differs; update the entry.
        log.info("Updating existing entry %r.", existing_title)
        # Remove the old key if the title changed.
        if existing_title != title:
            del catalogue[existing_title]

    # 5. Persist.
    catalogue[title] = url

    if dry_run:
        log.info("[dry-run] Would add: %r → %s", title, url)
        log.info("[dry-run] Catalogue path: %s", json_path)
        return 0

    save_catalogue(json_path, catalogue)
    log.info('Saved: "%s" → %s', title, url)
    log.info("Catalogue: %s  (%d entries total)", json_path, len(catalogue))
    return 0


def main(argv: list[str] | None = None) -> None:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    _configure_logging(args.verbose)

    raw_url: str = args.url or input("Enter YouTube video or playlist URL: ").strip()
    if not raw_url:
        log.error("No URL provided.")
        sys.exit(1)

    sys.exit(run(raw_url, args.json_path, dry_run=args.dry_run))


if __name__ == "__main__":
    main()