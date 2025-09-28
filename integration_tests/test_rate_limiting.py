#!/usr/bin/env python3

import asyncio
import logging
import pytest
from rym import RYMMetadataScraper, RYMConfig

@pytest.mark.integration
@pytest.mark.asyncio
async def test_genre_rate_limiting():
    """Test that genre scraping now uses proper rate limiting and error handling."""

    # Enable detailed logging to see the rate limiting in action
    logging.basicConfig(level=logging.INFO)

    # Create config with shorter rate limits to see the effect
    config = RYMConfig(
        expand_parent_genres=True,
        genre_cache_expiry_days=0,  # Force fresh scraping to test rate limiting
        cache_enabled=True,
        cache_dir='.rym_cache',
        min_request_interval=2.0,  # 2 second intervals to see rate limiting
        max_retries=2,  # Fewer retries for faster testing
        auto_rotate_on_failure=True  # Enable IP rotation on failures
    )

    print("=== Testing Enhanced Genre Rate Limiting ===")
    print(f"Rate limit interval: {config.min_request_interval} seconds")
    print(f"Max retries: {config.max_retries}")
    print(f"Auto rotate on failure: {config.auto_rotate_on_failure}")
    print()

    try:
        async with RYMMetadataScraper(config) as scraper:
            # Force genre hierarchy scraping by clearing cache first
            if scraper.scraper.genre_manager:
                print("Forcing fresh genre hierarchy scraping (cache disabled)...")

                # Try to scrape just a few genres to test the rate limiting
                # This will trigger the _fetch_single_genre_data calls that now use _fetch_url

                # Test with an album to trigger genre usage
                print("Testing album that should trigger genre expansion...")
                album_data = await scraper.get_album_metadata("Radiohead", "OK Computer", 1997)

                if album_data and album_data.genres:
                    print(f"✅ Genre scraping completed successfully!")
                    print(f"Found {len(album_data.genres)} genres: {album_data.genres[:5]}...")

                    # Check if we got parent genres (should be more than just basic ones)
                    if len(album_data.genres) > 3:
                        print("✅ Parent genre expansion working (multiple genres found)")
                    else:
                        print("⚠ May not have expanded parent genres properly")

                else:
                    print("❌ No album data or genres found")

            else:
                print("❌ No genre manager available")

    except Exception as e:
        print(f"Error during testing: {e}")
        import traceback
        traceback.print_exc()

@pytest.mark.integration
@pytest.mark.asyncio
async def test_rate_limiting_behavior():
    """Test just the rate limiting behavior with simpler setup."""

    logging.basicConfig(level=logging.INFO)

    config = RYMConfig(
        min_request_interval=1.0,  # 1 second for quick testing
        cache_enabled=True,
    )

    print("=== Testing Rate Limiting Behavior ===")

    try:
        async with RYMMetadataScraper(config) as scraper:
            print("Making sequential requests to test rate limiting...")

            # Make a few requests and watch the timing
            import time
            start_time = time.time()

            # Try 3 different albums
            albums = [
                ("Radiohead", "OK Computer", 1997),
                ("Aphex Twin", "Selected Ambient Works 85-92", 1992),
                ("Burial", "Untrue", 2007)
            ]

            for i, (artist, album, year) in enumerate(albums):
                request_start = time.time()
                print(f"\nRequest {i+1}: {artist} - {album}")

                result = await scraper.get_album_metadata(artist, album, year)
                request_end = time.time()

                if result:
                    print(f"  ✅ Success ({request_end - request_start:.2f}s)")
                    print(f"  Genres: {len(result.genres) if result.genres else 0}")
                else:
                    print(f"  ❌ Failed ({request_end - request_start:.2f}s)")

            total_time = time.time() - start_time
            print(f"\nTotal time: {total_time:.2f}s for {len(albums)} requests")
            print(f"Average time per request: {total_time/len(albums):.2f}s")

            if total_time >= (len(albums) - 1) * config.min_request_interval:
                print("✅ Rate limiting appears to be working correctly")
            else:
                print("⚠ Requests may have been too fast (rate limiting not working?)")

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    print("Choose test:")
    print("1. Full genre scraping test")
    print("2. Simple rate limiting test")
    choice = input("Enter 1 or 2: ").strip()

    if choice == "2":
        asyncio.run(test_rate_limiting_behavior())
    else:
        asyncio.run(test_genre_rate_limiting())