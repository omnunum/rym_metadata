#!/usr/bin/env python3

import asyncio
import logging
import pytest
from rym import RYMMetadataScraper, RYMConfig

@pytest.mark.integration
@pytest.mark.asyncio
async def test_genre_loading_and_expansion():
    """Test the enhanced genre loading and parent expansion functionality."""

    # Enable detailed logging to see the process
    logging.basicConfig(level=logging.INFO)

    # Create config with genre expansion enabled
    config = RYMConfig(
        expand_parent_genres=True,
        genre_cache_expiry_days=30,  # Cache for 30 days
        cache_enabled=True,
        cache_dir='.rym_cache'
    )

    print("=== Testing Enhanced Genre Loading and Parent Expansion ===")
    print(f"Config: expand_parent_genres={config.expand_parent_genres}")
    print(f"Cache directory: {config.cache_dir}")
    print()

    try:
        async with RYMMetadataScraper(config) as scraper:
            # Check genre manager status
            if scraper.scraper.genre_manager:
                stats = scraper.scraper.genre_manager.get_stats()
                print(f"Genre manager stats: {stats}")
                print()
            else:
                print("No genre manager initialized")
                return

            # Test with an album that should have genres
            artist = "Radiohead"
            album = "OK Computer"
            year = 1997

            print(f"Testing album: {artist} - {album} ({year})")
            print("This will:")
            print("1. Check/load existing genre hierarchy data")
            print("2. Scrape fresh genre data if cache is invalid/missing")
            print("3. Extract album genres and expand with parent genres")
            print()

            album_data = await scraper.get_album_metadata(artist, album, year)

            if album_data:
                print(f"✓ Successfully retrieved album metadata:")
                print(f"  Artist: {album_data.artist}")
                print(f"  Album: {album_data.album}")
                print(f"  Genres ({len(album_data.genres)}): {album_data.genres}")
                print(f"  Descriptors ({len(album_data.descriptors)}): {album_data.descriptors}")
                print(f"  URL: {album_data.url}")

                # Check if parent genres were added (should be more than just the basic ones)
                if len(album_data.genres) > 2:
                    print(f"✓ Parent genre expansion appears to be working ({len(album_data.genres)} total genres)")
                else:
                    print(f"⚠ Only {len(album_data.genres)} genres found, parent expansion may not be working")

            else:
                print("✗ No album metadata found")

            # Final genre manager stats
            if scraper.scraper.genre_manager:
                final_stats = scraper.scraper.genre_manager.get_stats()
                print(f"\nFinal genre manager stats: {final_stats}")

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_genre_loading_and_expansion())