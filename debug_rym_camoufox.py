#!/usr/bin/env python3

import asyncio
import logging
from beetsplug_rym_camoufox import RYMCamoufoxPlugin
from camoufox import AsyncCamoufox
# RYMSearchEngine is now integrated into RYMScraper

async def debug_album_async():
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

                # Test integrated search functionality
                album_url = await plugin.scraper._search_album_url(search_url, page, artist, album, year)
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

def debug_album():
    """Sync wrapper for async debug function."""
    asyncio.run(debug_album_async())

if __name__ == "__main__":
    debug_album()