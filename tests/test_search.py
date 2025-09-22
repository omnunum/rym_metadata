"""Tests for RYM search engine and fuzzy matching functionality."""

import pytest
from unittest.mock import AsyncMock, Mock
from bs4 import BeautifulSoup
from rym.scraper import RYMScraper, _normalize_artist_name, _is_exact_artist_match


class TestRYMSearchEngine:
    """Test suite for RYM search functionality integrated into RYMScraper."""

    @pytest.fixture
    def mock_config(self):
        """Create mock config for scraper."""
        config = Mock()
        config.matching_threshold = 0.8
        config.max_retries = 3
        config.retry_delay = 1
        return config

    @pytest.fixture
    def scraper(self, mock_config):
        """Create scraper instance with mock config."""
        scraper = RYMScraper(mock_config)
        scraper.fetch_url_with_retry = AsyncMock()
        return scraper

    def test_string_similarity_exact_match(self, scraper):
        """Test string similarity with exact matches."""
        score = scraper._calculate_match_score(
            {'artist': 'Radiohead', 'album': 'OK Computer', 'year': 1997},
            'Radiohead',
            'OK Computer',
            1997
        )
        assert score == 1.0

    def test_string_similarity_case_insensitive(self, scraper):
        """Test string similarity is case insensitive."""
        score = scraper._calculate_match_score(
            {'artist': 'radiohead', 'album': 'ok computer', 'year': 1997},
            'Radiohead',
            'OK Computer',
            1997
        )
        assert score == 1.0


    def test_year_scoring_exact(self, scraper):
        """Test year scoring with exact match."""
        score = scraper._calculate_match_score(
            {'artist': 'Test', 'album': 'Test', 'year': 2000},
            'Test',
            'Test',
            2000
        )
        # Year component should be 1.0, overall score weighted accordingly
        assert score == 1.0


    def test_year_scoring_no_target_year(self, scraper):
        """Test year scoring when no target year provided."""
        score = scraper._calculate_match_score(
            {'artist': 'Test', 'album': 'Test', 'year': 2000},
            'Test',
            'Test',
            None
        )
        # Year component should be 1.0 (default)
        assert score == 1.0

    def test_year_scoring_no_candidate_year(self, scraper):
        """Test year scoring when candidate has no year."""
        score = scraper._calculate_match_score(
            {'artist': 'Test', 'album': 'Test', 'year': None},
            'Test',
            'Test',
            2000
        )
        # Year component should be 1.0 (default)
        assert score == 1.0






    @pytest.mark.asyncio
    async def test_search_album_url_success(self, scraper, sample_search_html):
        """Test successful album URL search."""
        scraper.fetch_url_with_retry.return_value = sample_search_html

        result = await scraper._search_album_url(
            "http://example.com/search",
            Mock(),  # page mock
            "Kollektiv Turmstrasse",
            "Musik Gewinnt Freunde Collection",
            2013
        )

        assert result == "http://rateyourmusic.com/release/album/kollektiv-turmstrasse/musik-gewinnt-freunde-collection/"




    @pytest.mark.asyncio
    async def test_search_album_url_best_match_selection(self, scraper):
        """Test that the best scoring match is selected."""
        html = '''
        <html>
        <body>
            <tr class="infobox">
                <td>
                    <table>
                        <tr>
                            <td><a class="artist" href="/artist/test">Different Artist</a></td>
                            <td><a class="searchpage" href="/release/album/different/album/">Different Album</a></td>
                            <td>2000</td>
                        </tr>
                    </table>
                    <table>
                        <tr>
                            <td><a class="artist" href="/artist/radiohead">Radiohead</a></td>
                            <td><a class="searchpage" href="/release/album/radiohead/ok-computer/">OK Computer</a></td>
                            <td>1997</td>
                        </tr>
                    </table>
                </td>
            </tr>
        </body>
        </html>
        '''
        scraper.fetch_url_with_retry.return_value = html

        result = await scraper._search_album_url(
            "http://example.com/search",
            Mock(),
            "Radiohead",
            "OK Computer",
            1997
        )

        # Should select the exact match (Radiohead - OK Computer)
        assert result == "http://rateyourmusic.com/release/album/radiohead/ok-computer/"

    @pytest.mark.asyncio
    async def test_search_album_url_real_cache_data(self, scraper, cache_fixtures_dir):
        """Test search with real cached search results."""
        if not list(cache_fixtures_dir.glob("*.json")):
            pytest.skip("No cache fixtures available")

        import json

        # Find a search result cache file
        search_cache = None
        for cache_file in cache_fixtures_dir.glob("*.json"):
            with open(cache_file, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)
            if 'search' in cache_data.get('url', ''):
                search_cache = cache_data
                break

        if not search_cache:
            pytest.skip("No search cache fixtures available")

        scraper.fetch_url_with_retry.return_value = search_cache['html']

        # Extract artist/album from the URL for testing
        # This is a basic test to ensure real data can be parsed
        result = await scraper._search_album_url(
            search_cache['url'],
            Mock(),
            "Kollektiv Turmstrasse",  # Using known artist from fixtures
            "Test Album"
        )

        # Should either find a match or return None (both are valid with real data)
        assert result is None or result.startswith("http://rateyourmusic.com")


class TestArtistExactMatching:
    """Test suite for exact artist name matching functionality."""

    def test_normalize_artist_name_basic(self):
        """Test basic artist name normalization."""
        assert _normalize_artist_name("Radiohead") == "radiohead"
        assert _normalize_artist_name("RADIOHEAD") == "radiohead"
        assert _normalize_artist_name("  Radiohead  ") == "radiohead"

    def test_normalize_artist_name_whitespace(self):
        """Test whitespace normalization."""
        assert _normalize_artist_name("Pink  Floyd") == "pink floyd"
        assert _normalize_artist_name("Pink\t\nFloyd") == "pink floyd"
        assert _normalize_artist_name("   Pink   Floyd   ") == "pink floyd"

    def test_normalize_artist_name_unicode(self):
        """Test Unicode and accent removal."""
        assert _normalize_artist_name("Sigur Rós") == "sigur ros"
        assert _normalize_artist_name("Björk") == "bjork"
        assert _normalize_artist_name("Café Tacvba") == "cafe tacvba"

    def test_is_exact_artist_match_positive_cases(self):
        """Test cases that should match exactly."""
        # Case insensitive
        assert _is_exact_artist_match("Radiohead", "radiohead")
        assert _is_exact_artist_match("RADIOHEAD", "Radiohead")

        # Whitespace normalization
        assert _is_exact_artist_match("Pink Floyd", "Pink  Floyd")
        assert _is_exact_artist_match("  Pink Floyd  ", "Pink Floyd")

        # Unicode normalization
        assert _is_exact_artist_match("Sigur Rós", "Sigur Ros")
        assert _is_exact_artist_match("Björk", "Bjork")

    def test_is_exact_artist_match_negative_cases(self):
        """Test cases that should NOT match."""
        # Different artists
        assert not _is_exact_artist_match("Radiohead", "Pink Floyd")
        assert not _is_exact_artist_match("Nonexistent Artist", "Nonexistent Night")
        assert not _is_exact_artist_match("Test Artist", "Different Artist")

        # Partial matches should not work
        assert not _is_exact_artist_match("Radio", "Radiohead")
        assert not _is_exact_artist_match("Radiohead", "Radio")

    @pytest.fixture
    def scraper(self):
        """Create scraper instance for artist search tests."""
        config = Mock()
        config.matching_threshold = 0.8
        scraper = RYMScraper(config)
        scraper.fetch_url_with_retry = AsyncMock()
        return scraper

    @pytest.mark.asyncio
    async def test_search_artist_url_exact_match_found(self, scraper):
        """Test artist search with exact match found."""
        html = '''
        <html>
        <body>
            <a class="searchpage" href="/artist/radiohead">Radiohead</a>
            <a class="searchpage" href="/artist/radio-dept">The Radio Dept.</a>
        </body>
        </html>
        '''
        scraper.fetch_url_with_retry.return_value = html

        result = await scraper._search_artist_url(
            "http://example.com/search",
            Mock(),
            "Radiohead"
        )

        assert result == "http://rateyourmusic.com/artist/radiohead"

    @pytest.mark.asyncio
    async def test_search_artist_url_no_exact_match(self, scraper):
        """Test artist search with no exact match (should return None)."""
        html = '''
        <html>
        <body>
            <a class="searchpage" href="/artist/nonexistent-night">Nonexistent Night</a>
            <a class="searchpage" href="/artist/some-other-artist">Some Other Artist</a>
        </body>
        </html>
        '''
        scraper.fetch_url_with_retry.return_value = html

        result = await scraper._search_artist_url(
            "http://example.com/search",
            Mock(),
            "Nonexistent Artist"  # This should NOT match "Nonexistent Night"
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_search_artist_url_case_insensitive_match(self, scraper):
        """Test artist search with case differences."""
        html = '''
        <html>
        <body>
            <a class="searchpage" href="/artist/radiohead">radiohead</a>
        </body>
        </html>
        '''
        scraper.fetch_url_with_retry.return_value = html

        result = await scraper._search_artist_url(
            "http://example.com/search",
            Mock(),
            "Radiohead"  # Different case
        )

        assert result == "http://rateyourmusic.com/artist/radiohead"

    @pytest.mark.asyncio
    async def test_search_artist_url_unicode_match(self, scraper):
        """Test artist search with Unicode normalization."""
        html = '''
        <html>
        <body>
            <a class="searchpage" href="/artist/sigur-ros">Sigur Ros</a>
        </body>
        </html>
        '''
        scraper.fetch_url_with_retry.return_value = html

        result = await scraper._search_artist_url(
            "http://example.com/search",
            Mock(),
            "Sigur Rós"  # With accent
        )

        assert result == "http://rateyourmusic.com/artist/sigur-ros"



