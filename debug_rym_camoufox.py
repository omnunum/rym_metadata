#!/usr/bin/env python3

import asyncio
import logging
import os
from rym import RYMMetadataScraper, RYMConfig

async def debug_album_async():
    """Test fetching genre info for a single album using the simplified API."""

    # Enable info level logging (use DEBUG for more verbose resource blocking logs)
    logging.basicConfig(level=logging.INFO)

    # Create config with proxy settings from environment variables
    config = RYMConfig(
        proxy_enabled=True,
        proxy_host=os.environ.get('PROXY_HOST'),
        proxy_port=int(os.environ.get('PROXY_PORT')) if os.environ.get('PROXY_PORT') else None,
        proxy_username=os.environ.get('PROXY_USERNAME'),
        proxy_password=os.environ.get('PROXY_PASSWORD'),
        auto_rotate_on_failure=True,
        expand_parent_genres=True
    )

    # Test with a well-known album
    artist = "X Club."
    album = "Stay With Me"
    year = 2025

    print(f"Testing: {artist} - {album}")
    if config.proxy_host and config.proxy_port:
        print(f"Using proxy: {config.proxy_host}:{config.proxy_port}")
    else:
        print("No proxy configured (env vars not set)")

    try:
        # Use the API with proxy-aware configuration
        async with RYMMetadataScraper(config) as scraper:
            print("Fetching album data...")
            album_data = await scraper.get_album_metadata(artist, album, year)

            if album_data:
                print(f"✓ Found album data:")
                print(f"  Artist: {album_data.artist}")
                print(f"  Album: {album_data.album}")
                print(f"  Genres: {album_data.genres}")
                print(f"  Descriptors: {album_data.descriptors}")
                print(f"  URL: {album_data.url}")
            else:
                print("✗ No album data found, trying artist fallback...")

                # User controls the fallback logic
                artist_data = await scraper.get_artist_metadata(artist)

                if artist_data:
                    print(f"✓ Found artist data:")
                    print(f"  Artist: {artist_data.artist}")
                    print(f"  Genres: {artist_data.genres}")
                    print(f"  Descriptors: {artist_data.descriptors}")
                    print(f"  URL: {artist_data.url}")
                else:
                    print("✗ No artist data found either")

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

async def debug_artist_only():
    """Test fetching genre info for an artist only."""

    logging.basicConfig(level=logging.INFO)

    # Create config with proxy settings from environment variables
    config = RYMConfig(
        proxy_enabled=True,
        proxy_host=os.environ.get('PROXY_HOST'),
        proxy_port=int(os.environ.get('PROXY_PORT')) if os.environ.get('PROXY_PORT') else None,
        proxy_username=os.environ.get('PROXY_USERNAME'),
        proxy_password=os.environ.get('PROXY_PASSWORD'),
        auto_rotate_on_failure=True,
        expand_parent_genres=True
    )

    artist = "Kollektiv Turmstrasse"
    print(f"Testing artist-only: {artist}")
    if config.proxy_host and config.proxy_port:
        print(f"Using proxy: {config.proxy_host}:{config.proxy_port}")
    else:
        print("No proxy configured (env vars not set)")

    try:
        async with RYMMetadataScraper(config) as scraper:
            artist_data = await scraper.get_artist_metadata(artist)

            if artist_data:
                print(f"✓ Found artist data:")
                print(f"  Artist: {artist_data.artist}")
                print(f"  Genres: {artist_data.genres}")
                print(f"  Descriptors: {artist_data.descriptors}")
                print(f"  URL: {artist_data.url}")
            else:
                print("✗ No artist data found")

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

def debug_album():
    """Sync wrapper for async debug function."""
    asyncio.run(debug_album_async())

def debug_artist():
    """Sync wrapper for async artist debug function."""
    asyncio.run(debug_artist_only())

async def debug_batch_with_session():
    """Test batch processing using a persistent browser session."""

    logging.basicConfig(level=logging.INFO)

    # Create config with proxy settings from environment variables
    config = RYMConfig(
        proxy_enabled=True,
        proxy_host=os.environ.get('PROXY_HOST'),
        proxy_port=int(os.environ.get('PROXY_PORT')) if os.environ.get('PROXY_PORT') else None,
        proxy_username=os.environ.get('PROXY_USERNAME'),
        proxy_password=os.environ.get('PROXY_PASSWORD'),
        auto_rotate_on_failure=True,
        expand_parent_genres=True
    )

    # Test multiple albums with one browser session
    test_items = [
        ("Kollektiv Turmstrasse", "Musik Gewinnt Freunde Collection", 2013),
        ("Radiohead", "OK Computer", 1997),
        ("Nonexistent Artist", "Fake Album", 2000)  # This should trigger artist fallback
    ]

    print("Testing batch processing with persistent session...")
    if config.proxy_host and config.proxy_port:
        print(f"Using proxy: {config.proxy_host}:{config.proxy_port}")
    else:
        print("No proxy configured (env vars not set)")

    try:
        # One browser session for all requests - very efficient!
        async with RYMMetadataScraper(config) as scraper:
            for i, (artist, album, year) in enumerate(test_items):
                print(f"\nProcessing {i+1}: {artist} - {album}")

                # Try album first
                album_data = await scraper.get_album_metadata(artist, album, year)
                if album_data:
                    print(f"  ✓ Found album data:")
                    print(f"    Genres: {album_data.genres}")
                    print(f"    Descriptors: {album_data.descriptors}")
                    print(f"    URL: {album_data.url}")
                else:
                    print(f"  ✗ No album data, trying artist fallback...")

                    # Fallback to artist (user controls this logic)
                    artist_data = await scraper.get_artist_metadata(artist)
                    if artist_data:
                        print(f"  ✓ Found artist data:")
                        print(f"    Genres: {artist_data.genres}")
                        print(f"    Descriptors: {artist_data.descriptors}")
                        print(f"    URL: {artist_data.url}")
                    else:
                        print(f"  ✗ No data found for {artist}")

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

def debug_batch():
    """Sync wrapper for async batch debug function."""
    asyncio.run(debug_batch_with_session())

if __name__ == "__main__":
    print("=== Simple API Demo ===")
    print("async with RYMMetadataScraper() as scraper:")
    print("    album_data = await scraper.get_album_metadata(artist, album, year)")
    print("    if not album_data:")
    print("        artist_data = await scraper.get_artist_metadata(artist)")
    print()

    print("=== Testing Single Album Fetch (with artist fallback) ===")
    debug_album()
    print("\n=== Testing Artist-Only Fetch ===")
    debug_artist()
    print("\n=== Testing Batch Processing with Persistent Session ===")
    debug_batch()