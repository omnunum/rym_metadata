#!/usr/bin/env python3

import os
import asyncio
import logging
from beetsplug_rym_camoufox import RYMCamoufoxPlugin
from camoufox import AsyncCamoufox

async def test_album_async():
    """Test fetching genre info for a single album using AsyncCamoufox."""

    # Set environment variables before importing plugin
    os.environ['BRIGHTDATA_USER'] = "brd-customer-hl_9c0cc071-zone-residential_proxy1"
    os.environ['BRIGHTDATA_PASS'] = "6ctcxp8tk57x"

    # Enable debug logging
    logging.basicConfig(level=logging.DEBUG)

    plugin = RYMCamoufoxPlugin()
    plugin._log.setLevel(logging.DEBUG)

    # Test with a well-known album
    artist = "Kollektiv Turmstrasse"
    album = "Musik Gewinnt Freunde Collection"
    year = 2013  

    print(f"Testing: {artist} - {album}")

    # Check credentials
    if not (plugin.proxy_user and plugin.proxy_pass):
        print("Warning: No proxy credentials found")
        print("Set PROXY_HOST, PROXY_USERNAME, and PROXY_PASSWORD environment variables")

    try:
        # Get browser options and create browser
        browser_options = plugin._get_browser_options()
        print("Browser options created successfully")

        async with AsyncCamoufox(**browser_options) as browser:
            # Create a page
            page = await browser.new_page()
            print("Browser page created successfully")

            # Build direct URL
            direct_url = plugin._build_direct_url(artist, album)
            print(f"Direct URL: {direct_url}")

            # Extract genres using new async method
            genres = await plugin._extract_genres_from_url_async(direct_url, page)
            print(f"Genres: {genres}")

            # Test search fallback if direct failed
            if not genres:
                print("Direct URL failed, trying search...")
                search_url = plugin._build_search_url(artist, album)
                print(f"Search URL: {search_url}")

                # Add year parameter for testing (realistic year for electronic music)
                album_url = await plugin._search_album_url_async(search_url, page, artist, album, year)
                print(f"Found album URL: {album_url}")

                if album_url:
                    genres = await plugin._extract_genres_from_url_async(album_url, page)
                    print(f"Genres from search: {genres}")

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

def test_album():
    """Sync wrapper for async test."""
    asyncio.run(test_album_async())

if __name__ == "__main__":
    test_album()