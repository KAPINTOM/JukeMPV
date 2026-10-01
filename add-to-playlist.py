#!/usr/bin/env python3
"""
add-to-playlist.py
==================
Add a YouTube video or playlist URL to a local ``playlists.json`` catalogue.
Supports regular YouTube links, ``youtu.be`` short-links, ``/shorts/`` and
``/embed/`` links, and ``music.youtube.com`` links (all rewritten to the
canonical ``www.youtube.com`` form so duplicates are detected reliably).

Usage
-----
Interactive (prompts for URL):
    python add-to-playlist.py

Non-interactive (pass URL directly):
    python add-to-playlist.py <url>

Optional flags:
    --name TEXT    Store the entry under this name instead of the YouTube title
                   (also skips the network lookup).
    --dry-run      Resolve the title but do not write to disk.
    --verbose      Print debug-level information.
    --json <path>  Override the default playlists.json path.
"""

from __future__ import annotations

import argparse
import http.client
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def _base_dir() -> str:
    """Directory holding the script — or the executable when frozen (PyInstaller)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


DEFAULT_JSON_PATH: str = os.path.join(_base_dir(), "playlists.json")

YOUTUBE_HOSTS: frozenset[str] = frozenset(
    {"www.youtube.com", "youtube.com", "youtu.be", "m.youtube.com", "music.youtube.com"}
)

# Tracking / noise query parameters stripped from stored URLs.
_STRIP_PARAMS: frozenset[str] = frozenset({"si", "pp", "feature", "ab_channel"})
_STRIP_PREFIXES: tuple[str, ...] = ("utm_",)

# Canonical query-parameter order: v, then list, then everything else A→Z.
_PARAM_ORDER: dict[str, int] = {"v": 0, "list": 1}

# /shorts/<id>, /embed/<id>, /live/<id>, /v/<id>  →  /watch?v=<id>
_VIDEO_ID_PATH = re.compile(r"^/(?:shorts|embed|live|v)/([A-Za-z0-9_-]{11})")

_REQUEST_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

_NETWORK_TIMEOUT: int = 15            # seconds per request attempt
_MAX_RETRIES: int = 3                 # total attempts before giving up
_RETRY_BACKOFF: float = 1.5           # seconds; doubles on each retry
_RETRYABLE_STATUS: frozenset[int] = frozenset({429, 500, 502, 503, 504})
_MAX_RESPONSE_BYTES: int = 8 * 1024 * 1024   # sanity cap on downloaded pages

_OEMBED_ENDPOINT = "https://www.youtube.com/oembed"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

log = logging.getLogger("add-to-playlist")


class _BelowLevel(logging.Filter):
    """Pass only records strictly below *level* (used to keep stdout/stderr apart)."""

    def __init__(self, level: int) -> None:
        super().__init__()
        self._level = level

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno < self._level


def _configure_logging(verbose: bool) -> None:
    """INFO/DEBUG go to stdout; WARNING and above go to stderr."""
    # Never crash on characters the console encoding can't represent.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(errors="replace")
            except (OSError, ValueError):
                pass

    formatter = logging.Formatter("%(levelname)s: %(message)s")

    out = logging.StreamHandler(sys.stdout)
    out.addFilter(_BelowLevel(logging.WARNING))
    out.setFormatter(formatter)

    err = logging.StreamHandler(sys.stderr)
    err.setLevel(logging.WARNING)
    err.setFormatter(formatter)

    log.handlers.clear()            # idempotent if called more than once
    log.addHandler(out)
    log.addHandler(err)
    log.setLevel(logging.DEBUG if verbose else logging.INFO)
    log.propagate = False


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def _parse(url: str):
    """``urlparse`` that tolerates a pasted link with no ``https://`` prefix."""
    url = url.strip()
    if "://" not in url:
        url = "https://" + url
    return urlparse(url)


def is_youtube_url(url: str) -> bool:
    """Return *True* only for http(s) links on recognised YouTube hostnames."""
    try:
        parsed = _parse(url)
        return parsed.scheme in ("http", "https") and (parsed.hostname or "").lower() in YOUTUBE_HOSTS
    except ValueError:
        return False


def _is_noise_param(key: str) -> bool:
    return key in _STRIP_PARAMS or key.startswith(_STRIP_PREFIXES)


def normalize_url(url: str) -> str:
    """
    Canonicalize a YouTube URL so semantically identical links share one form.

    Applied to YouTube hosts only (other http(s) URLs are returned untouched,
    since jukempv accepts any URL mpv can play):

      1. All YouTube hostnames → ``https://www.youtube.com``.
      2. ``youtu.be/<id>``, ``/shorts/<id>``, ``/embed/<id>`` → ``/watch?v=<id>``.
      3. Tracking parameters (``si``, ``pp``, ``utm_*`` …) and the fragment are dropped.
      4. Remaining parameters get a stable order (``v``, ``list``, then A→Z), so
         ``?list=X&v=Y`` and ``?v=Y&list=X`` compare equal.
    """
    url = url.strip()
    try:
        parsed = _parse(url)
        host = (parsed.hostname or "").lower()
    except ValueError:
        return url
    if host not in YOUTUBE_HOSTS:
        return url

    query = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if not _is_noise_param(k)
    ]
    path = parsed.path

    video_id = ""
    if host == "youtu.be":
        video_id = path.strip("/").split("/")[0]
    else:
        match = _VIDEO_ID_PATH.match(path)
        if match:
            video_id = match.group(1)

    if video_id:
        query = [(k, v) for k, v in query if k != "v"]
        query.append(("v", video_id))
        path = "/watch"
        log.debug("Rewrote %s path → /watch?v=%s", host, video_id)

    query.sort(key=lambda kv: (_PARAM_ORDER.get(kv[0], 2), kv[0]))
    return urlunparse(("https", "www.youtube.com", path, "", urlencode(query), ""))


def has_media_id(url: str) -> bool:
    """True if a (normalized) URL points at a specific video or playlist."""
    params = dict(parse_qsl(urlparse(url).query))
    return bool(params.get("v") or params.get("list"))


# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------

def fetch_text(url: str) -> str | None:
    """
    Fetch *url* and return the decoded body, or *None* on failure.

    Retries up to ``_MAX_RETRIES`` times with exponential back-off on
    transient failures (timeouts, dropped connections, 429 and 5xx).  Other
    client errors (404, 403 …) are not retried.
    """
    delay = _RETRY_BACKOFF

    for attempt in range(1, _MAX_RETRIES + 1):
        req = urllib.request.Request(url, headers=_REQUEST_HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=_NETWORK_TIMEOUT) as resp:
                raw = resp.read(_MAX_RESPONSE_BYTES)
                charset = resp.headers.get_content_charset() or "utf-8"
            try:
                text = raw.decode(charset, errors="replace")
            except LookupError:                 # server announced an unknown charset
                text = raw.decode("utf-8", errors="replace")
            if not text:
                log.warning("Response body was empty.")
                return None
            log.debug("Fetched %d bytes from %s.", len(raw), url)
            return text

        except urllib.error.HTTPError as exc:   # must precede URLError (subclass)
            reason = f"HTTP {exc.code} {exc.reason}"
            retryable = exc.code in _RETRYABLE_STATUS
            exc.close()
        except urllib.error.URLError as exc:
            reason = f"network error: {exc.reason}"
            retryable = True
        except (http.client.HTTPException, OSError) as exc:
            # Covers read timeouts, connection resets, truncated responses…
            reason = f"{type(exc).__name__}: {exc}"
            retryable = True

        if retryable and attempt < _MAX_RETRIES:
            log.warning("%s — retrying in %.1fs (attempt %d/%d)…",
                        reason, delay, attempt, _MAX_RETRIES)
            time.sleep(delay)
            delay *= 2
            continue

        log.error("Request failed: %s", reason)
        return None

    return None  # unreachable; keeps type checkers happy


# ---------------------------------------------------------------------------
# JSON-blob extraction
# ---------------------------------------------------------------------------

_JSON_DECODER = json.JSONDecoder()


def _assignment_patterns(var_name: str) -> tuple[re.Pattern[str], ...]:
    name = re.escape(var_name)
    return (
        # var ytInitialData = {…}   /   ytInitialData = {…}
        re.compile(rf"(?<![\w$.]){name}\s*=\s*(?=\{{)"),
        # window["ytInitialData"] = {…}
        re.compile(rf"""window\s*\[\s*["']{name}["']\s*\]\s*=\s*(?=\{{)"""),
    )


def _extract_json_blob(html: str, var_name: str) -> dict | None:
    """
    Extract the JSON object assigned to the JavaScript variable *var_name*.

    Uses ``JSONDecoder.raw_decode`` starting at the opening brace, so nested
    braces, braces inside strings and escape sequences are all handled by the
    real JSON parser rather than a hand-rolled scanner.
    """
    for pattern in _assignment_patterns(var_name):
        for match in pattern.finditer(html):
            try:
                obj, _end = _JSON_DECODER.raw_decode(html, match.end())
            except json.JSONDecodeError as exc:
                log.debug("JSON parse error in '%s': %s", var_name, exc)
                continue
            if isinstance(obj, dict):
                log.debug("Parsed '%s'.", var_name)
                return obj
    log.debug("No parsable assignment for '%s' found.", var_name)
    return None


# ---------------------------------------------------------------------------
# Title extraction
# ---------------------------------------------------------------------------

# Ordered (description, dotted-key-path) pairs tried against ytInitialData.
# Integer segments index into lists; everything else is a dict key.
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


def _deep_get(data: object, dotted_path: str) -> object | None:
    """
    Traverse nested dicts/lists using a dot-separated path.

    A segment is used as a list index when the current node is a list, and as
    a (string) dict key otherwise — so numeric dict keys still work.  Returns
    *None* on any missing key, bad index or type mismatch.
    """
    current = data
    for segment in dotted_path.split("."):
        if isinstance(current, list):
            try:
                current = current[int(segment)]
            except (ValueError, IndexError):
                return None
        elif isinstance(current, dict):
            if segment not in current:
                return None
            current = current[segment]
        else:
            return None
    return current


def clean_title(title: object) -> str | None:
    """Collapse whitespace; return *None* for anything that isn't a usable string."""
    if not isinstance(title, str):
        return None
    cleaned = " ".join(title.split())
    return cleaned or None


def _title_from_oembed(url: str) -> str | None:
    """Fallback: YouTube's public oEmbed endpoint (stable, tiny JSON response)."""
    endpoint = f"{_OEMBED_ENDPOINT}?{urlencode({'url': url, 'format': 'json'})}"
    body = fetch_text(endpoint)
    if not body:
        return None
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return None
    return clean_title(payload.get("title")) if isinstance(payload, dict) else None


def get_youtube_title(url: str) -> str | None:
    """
    Resolve the human-readable title of a YouTube video or playlist.

    Strategies, in order:
      1. ``ytInitialPlayerResponse.videoDetails.title`` — video pages only.
      2. Several paths inside ``ytInitialData`` — videos and playlists.
      3. The oEmbed endpoint, if the page loaded but its embedded data was
         missing or reshaped (YouTube changes this markup from time to time).
    """
    html = fetch_text(url)
    if not html:
        return None

    player_resp = _extract_json_blob(html, "ytInitialPlayerResponse")
    if player_resp:
        title = clean_title(_deep_get(player_resp, "videoDetails.title"))
        if title:
            log.debug("Title from ytInitialPlayerResponse: %r", title)
            return title

    initial_data = _extract_json_blob(html, "ytInitialData")
    if initial_data:
        for description, path in _INITIAL_DATA_TITLE_PATHS:
            title = clean_title(_deep_get(initial_data, path))
            if title:
                log.debug("Title from ytInitialData[%s]: %r", description, title)
                return title

    log.debug("Page scraping found no title; trying oEmbed.")
    title = _title_from_oembed(url)
    if title:
        log.debug("Title from oEmbed: %r", title)
        return title

    log.warning("Could not extract title from page.")
    return None


# ---------------------------------------------------------------------------
# Catalogue (playlists.json) management
# ---------------------------------------------------------------------------

class CatalogueError(Exception):
    """The catalogue exists but cannot be read safely."""


def load_catalogue(path: str) -> dict[str, str]:
    """
    Load the JSON catalogue from *path* (empty dict if it doesn't exist yet).

    A file that exists but is unreadable or malformed raises ``CatalogueError``
    and is left untouched — silently starting from an empty catalogue would
    overwrite the user's data on the next save.
    """
    if not os.path.isfile(path):
        log.debug("Catalogue not found at %s — starting fresh.", path)
        return {}
    try:
        # utf-8-sig tolerates the BOM that Windows Notepad adds.
        with open(path, encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CatalogueError(
            f"{path} is not valid JSON ({exc}). "
            "Fix or remove it and try again; the file was not modified."
        ) from None
    except OSError as exc:
        raise CatalogueError(f"Could not read {path}: {exc}") from None

    if not isinstance(data, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in data.items()
    ):
        raise CatalogueError(
            f"{path} must contain a flat JSON object of name → URL strings. "
            "The file was not modified."
        )
    log.debug("Loaded %d entries from %s.", len(data), path)
    return data


def save_catalogue(path: str, data: dict[str, str]) -> None:
    """
    Persist *data* to *path* atomically.

    Writes and fsyncs a sibling temporary file, then swaps it in with
    ``os.replace`` so an interrupted run can never leave a half-written file.
    Existing file permissions are preserved.
    """
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)

    tmp_fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".playlists-", suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        if os.path.exists(path):
            shutil.copymode(path, tmp_path)     # mkstemp creates 0600 files
        os.replace(tmp_path, path)
        log.debug("Catalogue saved atomically to %s.", path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def find_existing_entry(
    catalogue: dict[str, str], url: str
) -> tuple[str, str] | None:
    """
    Return ``(name, stored_url)`` if *url* is already in *catalogue*.

    Both sides are normalized first, so links that differ only in parameter
    order, tracking tokens or host variant count as duplicates.
    """
    canonical = normalize_url(url)
    for name, stored_url in catalogue.items():
        if normalize_url(stored_url) == canonical:
            return name, stored_url
    return None


def make_unique_name(catalogue: dict[str, str], name: str) -> str:
    """Append ``(2)``, ``(3)`` … so a new entry never overwrites a different one."""
    if name not in catalogue:
        return name
    n = 2
    while f"{name} ({n})" in catalogue:
        n += 1
    return f"{name} ({n})"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Add a YouTube video or playlist URL to playlists.json.",
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
        "--name",
        default=None,
        metavar="TEXT",
        help="Name to store the entry under (skips the title lookup).",
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
    name: str | None = None,
    dry_run: bool = False,
) -> int:
    """
    Core logic: validate → normalize → deduplicate → resolve title → persist.

    Returns 0 on success, 1 on any recoverable error.
    """
    # 1. Validate.
    raw_url = raw_url.strip()
    if not is_youtube_url(raw_url):
        log.error("Not a recognised YouTube URL: %s", raw_url)
        return 1

    # 2. Normalize.
    url = normalize_url(raw_url)
    if not has_media_id(url):
        log.error("URL doesn't point at a video or playlist: %s", raw_url)
        return 1
    if url != raw_url:
        log.info("Normalized URL: %s", url)

    # 3. Load the catalogue and check for duplicates *before* any network work.
    #    An existing entry is left exactly as the user has it — including any
    #    custom name they gave it.
    try:
        catalogue = load_catalogue(json_path)
    except CatalogueError as exc:
        log.error("%s", exc)
        return 1

    existing = find_existing_entry(catalogue, url)
    if existing is not None:
        log.info("Already in the catalogue as %r — no changes made.", existing[0])
        return 0

    # 4. Resolve the name.
    title = clean_title(name) if name else get_youtube_title(url)
    if not title:
        log.error("Could not determine a title.  Check the URL and your "
                  "connection, or supply one with --name.")
        return 1
    log.info("Title: %s", title)

    unique = make_unique_name(catalogue, title)
    if unique != title:
        log.warning("Another entry is already named %r; saving as %r.", title, unique)
        title = unique

    # 5. Persist.
    catalogue[title] = url

    if dry_run:
        log.info("[dry-run] Would add: %r → %s", title, url)
        log.info("[dry-run] Catalogue path: %s", json_path)
        return 0

    try:
        save_catalogue(json_path, catalogue)
    except OSError as exc:
        log.error("Could not write %s: %s", json_path, exc)
        return 1

    log.info('Saved: "%s" → %s', title, url)
    log.info("Catalogue: %s  (%d entries total)", json_path, len(catalogue))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    _configure_logging(args.verbose)

    raw_url = args.url
    if raw_url is None:
        try:
            raw_url = input("Enter YouTube video or playlist URL: ")
        except (EOFError, KeyboardInterrupt):
            print()
            log.error("No URL provided.")
            return 1

    if not raw_url.strip():
        log.error("No URL provided.")
        return 1

    return run(raw_url, args.json_path, name=args.name, dry_run=args.dry_run)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)