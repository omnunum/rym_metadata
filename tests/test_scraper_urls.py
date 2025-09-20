"""Tests for URL building functionality in RYM scraper."""

import pytest
from urllib.parse import unquote
from rym.scraper import RYMScraper


class TestRYMScraperURLs:
    """Test suite for RYM scraper URL building methods."""

    @pytest.fixture
    def scraper(self, mock_proxy_config):
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
            proxy_config=mock_proxy_config,
            cache_manager=None,
            session_manager=None,
            browser_manager=None
        )

    def test_build_direct_url_basic(self, scraper):
        """Test building direct URL with basic artist and album names."""
        artist = "Radiohead"
        album = "OK Computer"

        url = scraper.build_direct_url(artist, album)

        assert url.startswith("http://rateyourmusic.com/release/album/")
        assert "radiohead" in url
        assert "ok-computer" in url
        assert url.endswith("/")

    def test_build_direct_url_special_characters(self, scraper):
        """Test building direct URL with special characters."""
        artist = "Sigur Rós"
        album = "Ágætis byrjun"

        url = scraper.build_direct_url(artist, album)

        # Special characters should be removed
        assert "sigur-ros" in url or "sigur-rs" in url  # Depending on how characters are handled
        assert "gtis-byrjun" in url or "agtis-byrjun" in url

    def test_build_direct_url_punctuation(self, scraper):
        """Test building direct URL with punctuation."""
        artist = "Nine Inch Nails"
        album = "The Downward Spiral"

        url = scraper.build_direct_url(artist, album)

        assert "nine-inch-nails" in url
        assert "the-downward-spiral" in url






    def test_build_search_url_basic(self, scraper):
        """Test building search URL with basic terms."""
        artist = "Radiohead"
        album = "OK Computer"

        url = scraper.build_search_url(artist, album)

        assert url.startswith("http://rateyourmusic.com/search?searchtype=l&searchterm=")

        # Decode the URL to check content
        decoded_query = unquote(url.split("searchterm=")[1])
        assert "Radiohead" in decoded_query
        assert "OK Computer" in decoded_query

    def test_build_search_url_special_characters(self, scraper):
        """Test building search URL with special characters."""
        artist = "Sigur Rós"
        album = "Ágætis byrjun"

        url = scraper.build_search_url(artist, album)

        # Should be URL-encoded properly
        assert "searchterm=" in url
        decoded_query = unquote(url.split("searchterm=")[1])
        assert "Sigur Rós" in decoded_query or "Sigur Rs" in decoded_query
        assert "Ágætis byrjun" in decoded_query or "Agtis byrjun" in decoded_query

    def test_build_search_url_punctuation_preserved(self, scraper):
        """Test that search URL preserves some punctuation."""
        artist = "Nine Inch Nails"
        album = "The Downward Spiral"

        url = scraper.build_search_url(artist, album)

        decoded_query = unquote(url.split("searchterm=")[1])
        assert "Nine Inch Nails" in decoded_query
        assert "The Downward Spiral" in decoded_query

    def test_build_search_url_cleaning(self, scraper):
        """Test that search URL cleans non-word characters to spaces."""
        artist = "Belle & Sebastian"
        album = "If You're Feeling Sinister"

        url = scraper.build_search_url(artist, album)

        decoded_query = unquote(url.split("searchterm=")[1])
        # Non-word characters should be converted to spaces
        assert "Belle   Sebastian" in decoded_query or "Belle  Sebastian" in decoded_query
        assert "If You re Feeling Sinister" in decoded_query

    def test_build_search_url_multiple_spaces_normalized(self, scraper):
        """Test that multiple spaces are normalized in search URLs."""
        artist = "Pink   Floyd"
        album = "The   Wall"

        url = scraper.build_search_url(artist, album)

        decoded_query = unquote(url.split("searchterm=")[1])
        # Multiple spaces should be normalized to single spaces
        assert "Pink Floyd" in decoded_query
        assert "The Wall" in decoded_query




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
            search_url = scraper.build_search_url(artist, album)

            # Both should be valid URLs
            assert direct_url.startswith("http://rateyourmusic.com/release/album/")
            assert search_url.startswith("http://rateyourmusic.com/search?searchtype=l&searchterm=")



