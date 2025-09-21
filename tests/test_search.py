"""Tests for RYM search engine and fuzzy matching functionality."""

import pytest
from unittest.mock import AsyncMock, Mock
from bs4 import BeautifulSoup
from rym.scraper import RYMScraper


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



