#!/usr/bin/env python3

import asyncio
import logging
import json
import pytest
from rym import RYMMetadataScraper, RYMConfig
from rym.genre_manager import GenreHierarchyManager

@pytest.mark.integration
@pytest.mark.asyncio
async def test_genre_hierarchy():
    """Test the genre hierarchy functionality."""

    # Enable info level logging
    logging.basicConfig(level=logging.INFO)

    print("=== Testing Genre Hierarchy System ===\n")

    # Create config with genre expansion enabled
    config = RYMConfig(
        expand_parent_genres=True,
        genre_cache_expiry_days=30,
        cache_enabled=True,
        cache_dir='.rym_cache'
    )

    try:
        # Test the scraper with genre expansion
        async with RYMMetadataScraper(config) as scraper:
            print("✓ RYM Scraper initialized successfully")

            # Test genre manager functionality if available
            if scraper.scraper.genre_manager:
                genre_manager = scraper.scraper.genre_manager
                print("✓ Genre manager initialized")

                # Check if we have cached genre data
                if genre_manager.is_cache_valid():
                    print("✓ Genre hierarchy cache is valid")

                    # Load and test the hierarchy data
                    if genre_manager.load_hierarchy_data():
                        print("✓ Genre hierarchy data loaded successfully")

                        # Get some stats
                        stats = genre_manager.get_stats()
                        print(f"✓ Loaded {stats.get('total_genres', 0)} genres in hierarchy")
                        print(f"✓ Found {stats.get('top_level_genres', 0)} top-level genres")

                        # Test parent expansion with some common genres
                        test_genres = ["Dark Ambient", "Black Metal", "Acid Techno"]
                        print(f"\n--- Testing Parent Expansion ---")
                        print(f"Original genres: {test_genres}")

                        expanded = genre_manager.expand_genres_with_parents(test_genres)
                        print(f"Expanded genres: {expanded}")
                        print(f"Expansion: {len(test_genres)} -> {len(expanded)} genres")

                        # Test individual lookups
                        print(f"\n--- Testing Individual Genre Lookups ---")
                        for genre in test_genres:
                            genre_url = genre_manager.find_genre_url(genre)
                            if genre_url:
                                parents = genre_manager.get_all_parent_genres(genre_url)
                                parent_names = [genre_manager.get_genre_name(url) for url in parents if url]
                                parent_names = [name for name in parent_names if name]  # Filter None
                                print(f"{genre} -> URL: {genre_url}")
                                print(f"  Parents: {parent_names}")
                            else:
                                print(f"{genre} -> Not found in hierarchy")

                    else:
                        print("✗ Failed to load genre hierarchy data")
                        print("  This might be normal if genre data hasn't been scraped yet")

                        # Test scraping genre hierarchy
                        print("\n--- Testing Genre Hierarchy Scraping ---")
                        print("⚠  Warning: This will make many API requests and may take several minutes")

                        user_input = input("Do you want to scrape the genre hierarchy? (y/N): ")
                        if user_input.lower().startswith('y'):
                            print("🔄 Scraping genre hierarchy (this may take a while)...")
                            result = await scraper.scraper._scrape_genre_hierarchy()
                            if result:
                                print(f"✓ Genre hierarchy saved to: {result}")

                                # Try loading again
                                if genre_manager.load_hierarchy_data():
                                    stats = genre_manager.get_stats()
                                    print(f"✓ Successfully loaded {stats.get('total_genres', 0)} genres")
                                else:
                                    print("✗ Still failed to load after scraping")
                            else:
                                print("✗ Failed to scrape genre hierarchy")
                        else:
                            print("⏭  Skipped genre hierarchy scraping")
                else:
                    print("⚠  Genre hierarchy cache is not valid or doesn't exist")
                    print(f"   Cache file: {genre_manager.hierarchy_file_path}")
                    print(f"   Cache valid: {genre_manager.is_cache_valid()}")

            else:
                print("⚠  Genre manager not initialized (expansion disabled or no cache manager)")

            print(f"\n--- Testing Album Metadata with Genre Expansion ---")

            # Test album metadata extraction with a well-known album
            test_artist = "Burzum"
            test_album = "Filosofem"

            print(f"Testing: {test_artist} - {test_album}")
            album_data = await scraper.get_album_metadata(test_artist, test_album, 1996)

            if album_data:
                print(f"✓ Found album data:")
                print(f"  Original album: {album_data.album}")
                print(f"  Genres ({len(album_data.genres)}): {album_data.genres}")
                print(f"  Descriptors ({len(album_data.descriptors)}): {album_data.descriptors}")

                if len(album_data.genres) > 2:  # Likely expanded
                    print("✓ Genres appear to have been expanded with parents")
                else:
                    print("⚠  Genres may not have been expanded (could be normal)")
            else:
                print(f"✗ No album data found for {test_artist} - {test_album}")

    except Exception as e:
        print(f"✗ Error during testing: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    print("Genre Hierarchy System Test")
    print("This test will check if the genre hierarchy system is working correctly.")
    print("It will test genre expansion, parent lookups, and integration with album processing.\n")

    asyncio.run(test_genre_hierarchy())