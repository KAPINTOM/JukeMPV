#!/usr/bin/env python3
"""
Add a YouTube link to playlists.json using yt-dlp.
The entry key is the video/playlist title, the value is the URL.
"""

import json
import os
import subprocess
import sys

# Path to the JSON file (same directory as this script)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
JSON_PATH = os.path.join(SCRIPT_DIR, "playlists.json")

def get_youtube_title(url):
    """Return the title (video or playlist) for the given YouTube URL using yt-dlp."""
    # Command: get JSON info without downloading, flat mode for playlists
    cmd = ["yt-dlp", "-j", "--skip-download", "--flat-playlist", url]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        # For playlists, the key 'playlist_title' exists; for videos, use 'title'
        return data.get("playlist_title") or data.get("title")
    except subprocess.CalledProcessError as e:
        print(f"yt-dlp error (exit {e.returncode}): {e.stderr.strip()}")
    except json.JSONDecodeError:
        print(f"Failed to parse yt-dlp output for: {url}")
    except FileNotFoundError:
        print("yt-dlp command not found. Please install yt-dlp.")
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
        f.write("\n")  # trailing newline

def main():
    link = input("Enter YouTube video or playlist URL: ").strip()
    if not link:
        print("No URL provided.")
        return

    title = get_youtube_title(link)
    if not title:
        print("Could not retrieve title. Check the URL and your network.")
        return

    # Update JSON
    data = load_json(JSON_PATH)
    data[title] = link
    save_json(JSON_PATH, data)

    print(f"Added / updated: \"{title}\" -> {link}")
    print(f"Saved to {JSON_PATH}")

if __name__ == "__main__":
    main()