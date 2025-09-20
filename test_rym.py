#!/usr/bin/env python3

import asyncio
import os
import logging
import aiohttp
from beetsplug_rym import RYMPlugin

async def test_album():
    """Test fetching genre info for a single album."""

    # Enable debug logging
    logging.basicConfig(level=logging.DEBUG)

    plugin = RYMPlugin()
    plugin._log.setLevel(logging.DEBUG)

    # Test with a well-known album
    artist = "Radiohead"
    album = "OK Computer"

    print(f"Testing: {artist} - {album}")

    # Build search URL
    search_url = plugin._build_search_url(artist, album)
    print(f"Search URL: {search_url}")

    # Create session and test
    timeout = aiohttp.ClientTimeout(total=plugin.config['request_timeout'].get())
    async with aiohttp.ClientSession(timeout=timeout) as session:
        # Search for album URL
        album_url = await plugin._search_album_url(search_url, session)
        print(f"Album URL: {album_url}")

        if album_url:
            # Extract genres
            genres = await plugin._extract_genres_from_url(album_url, session)
            print(f"Genres: {genres}")
        else:
            print("No album URL found")

if __name__ == "__main__":
    asyncio.run(test_album())