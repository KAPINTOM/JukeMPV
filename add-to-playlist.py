#!/usr/bin/env python3
"""
Add a YouTube link to playlists.json using only the standard library.
Works for both videos and playlists by parsing embedded JSON from the page.
"""

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
JSON_PATH = os.path.join(SCRIPT_DIR, "playlists.json")

def clean_url(url):
    """Remove tracking parameters like 'si' from a YouTube URL."""
    parsed = urlparse(url)
    query_dict = parse_qs(parsed.query, keep_blank_values=True)
    if 'si' in query_dict:
        print("Removing 'si' tracking parameter from URL...")
        del query_dict['si']
    new_query = urlencode(query_dict, doseq=True) if query_dict else ""
    cleaned_url = urlunparse(parsed._replace(query=new_query))
    return cleaned_url

def fetch_html(url):
    """Fetch the HTML content of a YouTube page."""
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            return response.read().decode('utf-8')
    except urllib.error.URLError as e:
        print(f"Network error: {e}")
        return None

def extract_json_blob(html, var_name):
    """Extract a JavaScript JSON blob like 'var ytInitialData = {...};'."""
    # Use double braces {{ and }} to escape literal curly braces in the f‑string
    pattern = re.compile(rf'var {var_name} = ({{.+?}});', re.DOTALL)
    match = pattern.search(html)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    return None

def get_youtube_title(url):
    """Extract title from a YouTube video or playlist URL."""
    cleaned_url = clean_url(url)
    html = fetch_html(cleaned_url)
    if not html:
        return None

    # 1. Try ytInitialPlayerResponse (present on video pages)
    player_response = extract_json_blob(html, "ytInitialPlayerResponse")
    if player_response:
        try:
            title = player_response.get("videoDetails", {}).get("title")
            if title:
                return title
        except (KeyError, AttributeError):
            pass

    # 2. Try ytInitialData (used by both videos and playlists)
    initial_data = extract_json_blob(html, "ytInitialData")
    if initial_data:
        # For videos, the title may be inside videoDetails (sometimes present here too)
        try:
            title = initial_data.get("videoDetails", {}).get("title")
            if title:
                return title
        except (KeyError, AttributeError):
            pass

        # For playlists: try the metadata path first (most reliable)
        try:
            title = initial_data["metadata"]["playlistMetadataRenderer"]["title"]
            if title:
                return title
        except (KeyError, TypeError):
            pass

        # Another playlist path (deeper for some layouts)
        try:
            contents = initial_data["contents"]["twoColumnBrowseResultsRenderer"]["tabs"][0]["tabRenderer"]["content"]["sectionListRenderer"]["contents"][0]["itemSectionRenderer"]["contents"][0]["playlistHeaderRenderer"]["title"]["runs"][0]["text"]
            return contents
        except (KeyError, IndexError, TypeError):
            pass

        # For videos: sometimes the title is inside 'title' under 'microformat'
        try:
            title = initial_data["microformat"]["microformatDataRenderer"]["title"]
            if title:
                return title
        except (KeyError, TypeError):
            pass

    print("Could not extract title from the page.")
    return None

def load_json(file_path):
    """Load existing JSON, return empty dict if file missing or broken."""
    if not os.path.isfile(file_path):
        return {}
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        print(f"Warning: {file_path} is corrupted. Starting fresh.")
        return {}

def save_json(file_path, data):
    """Save dictionary to JSON with indentation."""
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")

def main():
    link = input("Enter YouTube video or playlist URL: ").strip()
    if not link:
        print("No URL provided.")
        return

    title = get_youtube_title(link)
    if not title:
        print("Could not retrieve title. Check the URL and your network.")
        return

    data = load_json(JSON_PATH)
    data[title] = link
    save_json(JSON_PATH, data)

    print(f"Added / updated: \"{title}\" -> {link}")
    print(f"Saved to {JSON_PATH}")

if __name__ == "__main__":
    main()
