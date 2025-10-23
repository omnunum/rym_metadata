#!/usr/bin/env python3
"""
Filter failed_flacs file to show only unique albums with failed tracks.
Excludes missing tracks and shows results at album level.
"""

import os
import sys
from pathlib import Path

def extract_album_path(track_path):
    """Extract album directory from full track path."""
    # Remove ANSI color codes
    clean_path = track_path.replace('\x1b[1;31m', '').replace('\x1b[39;49;00m', '')

    # Get the directory containing the track
    track_file = Path(clean_path)
    album_dir = track_file.parent

    return str(album_dir)

def main():
    failed_flacs_path = "/Volumes/downloads/music/failed_flacs"
    output_path = "/Volumes/downloads/music/failed_albums"

    if not os.path.exists(failed_flacs_path):
        print(f"Error: {failed_flacs_path} not found")
        return 1

    failed_albums = set()

    with open(failed_flacs_path, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            line = line.strip()

            # Look for lines with file paths (contain .flac and colon)
            if '.flac' in line and ':' in line:
                # Extract the file path (before the colon)
                file_path = line.split(':')[0]
                album_path = extract_album_path(file_path)
                failed_albums.add(album_path)

    # Write results to output file
    with open(output_path, 'w', encoding='utf-8') as f:
        for album in sorted(failed_albums):
            f.write(album + '\n')

    print(f"Wrote {len(failed_albums)} failed albums to {output_path}")

if __name__ == "__main__":
    sys.exit(main())