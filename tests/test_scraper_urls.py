"""Tests for URL building functionality in RYM scraper."""

import pytest
from rym.scraper import RYMScraper


class TestRYMScraperURLs:
    """Test suite for RYM scraper URL building methods."""

    @pytest.fixture
    def scraper(self):
        """Create a minimal scraper instance for URL testing."""
        # Mock config object
        class MockConfig:
            def __getitem__(self, key):
                return MockConfigItem()

        class MockConfigItem:
            def get(self):
                return 3  # For max_retries, retry_delay etc.

        return RYMScraper(
            config=MockConfig(),
            cache_manager=None,
            session_manager=None,
            browser_manager=None
        )

    def test_real_world_artist_names(self, scraper):
        """Test with real-world complex artist names."""
        test_cases = [
            ("!!!!", "Myth Takes"),
            ("65daysofstatic", "The Fall of Math"),
            ("Godspeed You! Black Emperor", "Lift Your Skinny Fists Like Antennas to Heaven"),
            ("Kollektiv Turmstrasse", "Musik Gewinnt Freunde Collection"),
            ("$uicideboy$", "I Want to Die in New Orleans"),
        ]

        for artist, album in test_cases:
            direct_url = scraper.build_direct_url(artist, album)

            # Should be valid URL
            assert direct_url.startswith("https://rateyourmusic.com/release/album/")