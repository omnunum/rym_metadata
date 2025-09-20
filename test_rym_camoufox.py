#!/usr/bin/env python3

import asyncio
import logging
from beetsplug_rym_camoufox import RYMCamoufoxPlugin
from camoufox import AsyncCamoufox
from rym.search import RYMSearchEngine

async def test_album_async():
    """Test fetching genre info for a single album using AsyncCamoufox."""

    # Enable info level logging (use DEBUG for more verbose resource blocking logs)
    logging.basicConfig(level=logging.INFO)

    plugin = RYMCamoufoxPlugin()
    plugin._log.setLevel(logging.INFO)

    # Test with a well-known album
    artist = "Kollektiv Turmstrasse"
    album = "Musik Gewinnt Freunde Collection"
    year = 2013

    print(f"Testing: {artist} - {album}")

    # Check credentials
    if not plugin.proxy_config.is_valid:
        print("Warning: No proxy credentials found")
        print("Configure proxy settings in beets config: proxy_host, proxy_port, proxy_username, proxy_password")

    try:
        # Get browser options and create browser
        browser_options = plugin.browser_manager.get_browser_options()
        print("Browser options created successfully")

        async with AsyncCamoufox(**browser_options) as browser:
            # Create a page
            page = await browser.new_page()
            print("Browser page created successfully")

            # Build direct URL
            direct_url = plugin.scraper.build_direct_url(artist, album)
            print(f"Direct URL: {direct_url}")

            # Extract genres using new async method
            genre_data = await plugin.scraper.extract_genres_from_url(direct_url, page)
            genres = genre_data.get('genres', [])
            descriptors = genre_data.get('descriptors', [])
            print(f"Genres: {genres}")
            print(f"Descriptors: {descriptors}")

            # Test search fallback if direct failed
            if not genres:
                print("Direct URL failed, trying search...")
                search_url = plugin.scraper.build_search_url(artist, album)
                print(f"Search URL: {search_url}")

                # Create search engine and test search
                search_engine = RYMSearchEngine(plugin.scraper)
                album_url = await search_engine.search_album_url(search_url, page, artist, album, year)
                print(f"Found album URL: {album_url}")

                if album_url:
                    genre_data = await plugin.scraper.extract_genres_from_url(album_url, page)
                    genres = genre_data.get('genres', [])
                    descriptors = genre_data.get('descriptors', [])
                    print(f"Genres from search: {genres}")
                    print(f"Descriptors from search: {descriptors}")

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

def test_album():
    """Sync wrapper for async test."""
    asyncio.run(test_album_async())

if __name__ == "__main__":
    test_album()