#!/usr/bin/env python3
"""
Add a YouTube link to playlists.json using only the standard library.
Works for both videos and playlists by parsing embedded JSON from the page.
"""

import json
import os
import shutil
import urllib.error
import urllib.parse
import urllib.request
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
JSON_PATH = os.path.join(SCRIPT_DIR, "playlists.json")

# A realistic browser User-Agent so YouTube serves the full page instead of a
# bot-challenge / consent redirect (fixes: fetch_html returned empty content).
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

YOUTUBE_HOSTS = {"www.youtube.com", "youtube.com", "youtu.be", "m.youtube.com"}


def is_youtube_url(url: str) -> bool:
    """Return True only for recognised YouTube hostnames."""
    try:
        host = urlparse(url).netloc.lower()
        return host in YOUTUBE_HOSTS
    except Exception:
        return False


def clean_url(url: str) -> str:
    """Remove tracking parameters (e.g. 'si') from a YouTube URL."""
    parsed = urlparse(url)
    query_dict = parse_qs(parsed.query, keep_blank_values=True)
    if "si" in query_dict:
        print("Removing 'si' tracking parameter from URL...")
        del query_dict["si"]
    new_query = urlencode(query_dict, doseq=True) if query_dict else ""
    return urlunparse(parsed._replace(query=new_query))


def fetch_html(url: str) -> str | None:
    """Fetch the HTML content of a YouTube page with browser-like headers."""
    req = urllib.request.Request(url, headers=_HEADERS)
    try:
        # FIX: catch HTTPError before the broader URLError so we can log the
        # status code (e.g. 403 Too Many Requests, 429 Rate Limited).
        with urllib.request.urlopen(req, timeout=15) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        print(f"HTTP error {e.code}: {e.reason}")
        return None
    except urllib.error.URLError as e:
        print(f"Network error: {e.reason}")
        return None


def extract_json_blob(html: str, var_name: str) -> dict | None:
    """
    Extract a JavaScript JSON blob like 'var ytInitialData = {...};'.

    FIX: the previous regex `({.+?});` with non-greedy matching stopped at the
    *first* `};` anywhere inside the blob (e.g. inside a nested object), so
    `json.loads` always received a truncated string and silently returned None.

    We now locate the opening `{` and walk character-by-character, tracking
    brace depth, to find the true closing brace.  String literals are skipped
    so braces inside them do not affect the depth count.
    """
    marker = f"var {var_name} = {{"
    start = html.find(marker)
    if start == -1:
        return None

    # `start` points at the opening `{` of the blob.
    start += len(marker) - 1  # rewind to include the `{`
    depth = 0
    in_string = False
    escape_next = False

    for i in range(start, len(html)):
        ch = html[i]

        if escape_next:
            escape_next = False
            continue

        if ch == "\\" and in_string:
            escape_next = True
            continue

        if ch == '"' and not escape_next:
            in_string = not in_string
            continue

        if in_string:
            continue

        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(html[start : i + 1])
                except json.JSONDecodeError as exc:
                    print(f"JSON parse error in {var_name}: {exc}")
                    return None

    return None  # no matching closing brace found


def get_youtube_title(url: str) -> str | None:
    """Extract title from a YouTube video or playlist URL."""
    html = fetch_html(url)
    if not html:
        return None

    # 1. Try ytInitialPlayerResponse (present on video pages)
    player_response = extract_json_blob(html, "ytInitialPlayerResponse")
    if player_response:
        title = player_response.get("videoDetails", {}).get("title")
        if title:
            return title

    # 2. Try ytInitialData (used by both videos and playlists)
    initial_data = extract_json_blob(html, "ytInitialData")
    if initial_data:
        # Video: videoDetails path
        title = initial_data.get("videoDetails", {}).get("title")
        if title:
            return title

        # Playlist: metadata path (most reliable)
        try:
            title = initial_data["metadata"]["playlistMetadataRenderer"]["title"]
            if title:
                return title
        except (KeyError, TypeError):
            pass

        # Playlist: deeper layout path
        try:
            title = (
                initial_data["contents"]
                ["twoColumnBrowseResultsRenderer"]["tabs"][0]
                ["tabRenderer"]["content"]["sectionListRenderer"]["contents"][0]
                ["itemSectionRenderer"]["contents"][0]
                ["playlistHeaderRenderer"]["title"]["runs"][0]["text"]
            )
            if title:
                return title
        except (KeyError, IndexError, TypeError):
            pass

        # Video: microformat fallback
        try:
            title = initial_data["microformat"]["microformatDataRenderer"]["title"]
            if title:
                return title
        except (KeyError, TypeError):
            pass

    print("Could not extract title from the page.")
    return None


def load_json(file_path: str) -> dict:
    """
    Load existing JSON; return empty dict if the file is missing.

    FIX: on corruption, back up the original file before returning an empty
    dict so the user's data is not silently overwritten.
    """
    if not os.path.isfile(file_path):
        return {}
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        backup = file_path + ".bak"
        shutil.copy2(file_path, backup)
        print(f"Warning: {file_path} is corrupted. A backup was saved to {backup}.")
        return {}


def save_json(file_path: str, data: dict) -> None:
    """Save dictionary to JSON with indentation."""
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def main() -> None:
    raw_link = input("Enter YouTube video or playlist URL: ").strip()
    if not raw_link:
        print("No URL provided.")
        return

    # FIX: validate that this is actually a YouTube URL before hitting the network.
    if not is_youtube_url(raw_link):
        print("Error: the URL does not appear to be a YouTube link.")
        return

    # FIX: clean the URL once here and use the cleaned version everywhere,
    # including what gets stored in the JSON file.
    link = clean_url(raw_link)

    title = get_youtube_title(link)
    if not title:
        print("Could not retrieve title. Check the URL and your network.")
        return

    data = load_json(JSON_PATH)
    data[title] = link
    save_json(JSON_PATH, data)

    print(f'Added / updated: "{title}" -> {link}')
    print(f"Saved to {JSON_PATH}")


if __name__ == "__main__":
    main()
