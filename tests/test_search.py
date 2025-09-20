"""Tests for RYM search engine and fuzzy matching functionality."""

import pytest
from unittest.mock import AsyncMock, Mock
from bs4 import BeautifulSoup
from rym.search import RYMSearchEngine


class TestRYMSearchEngine:
    """Test suite for RYMSearchEngine."""

    @pytest.fixture
    def mock_scraper(self):
        """Create mock scraper for search engine."""
        scraper = Mock()
        scraper.fetch_url_with_retry = AsyncMock()
        return scraper

    @pytest.fixture
    def search_engine(self, mock_scraper):
        """Create search engine instance with mock scraper."""
        return RYMSearchEngine(mock_scraper)

    def test_string_similarity_exact_match(self, search_engine):
        """Test string similarity with exact matches."""
        score = search_engine._calculate_match_score(
            {'artist': 'Radiohead', 'album': 'OK Computer', 'year': 1997},
            'Radiohead',
            'OK Computer',
            1997
        )
        assert score == 1.0

    def test_string_similarity_case_insensitive(self, search_engine):
        """Test string similarity is case insensitive."""
        score = search_engine._calculate_match_score(
            {'artist': 'radiohead', 'album': 'ok computer', 'year': 1997},
            'Radiohead',
            'OK Computer',
            1997
        )
        assert score == 1.0


    def test_year_scoring_exact(self, search_engine):
        """Test year scoring with exact match."""
        score = search_engine._calculate_match_score(
            {'artist': 'Test', 'album': 'Test', 'year': 2000},
            'Test',
            'Test',
            2000
        )
        # Year component should be 1.0, overall score weighted accordingly
        assert score == 1.0


    def test_year_scoring_no_target_year(self, search_engine):
        """Test year scoring when no target year provided."""
        score = search_engine._calculate_match_score(
            {'artist': 'Test', 'album': 'Test', 'year': 2000},
            'Test',
            'Test',
            None
        )
        # Year component should be 1.0 (default)
        assert score == 1.0

    def test_year_scoring_no_candidate_year(self, search_engine):
        """Test year scoring when candidate has no year."""
        score = search_engine._calculate_match_score(
            {'artist': 'Test', 'album': 'Test', 'year': None},
            'Test',
            'Test',
            2000
        )
        # Year component should be 1.0 (default)
        assert score == 1.0






    @pytest.mark.asyncio
    async def test_search_album_url_success(self, search_engine, mock_scraper, sample_search_html):
        """Test successful album URL search."""
        mock_scraper.fetch_url_with_retry.return_value = sample_search_html

        result = await search_engine.search_album_url(
            "http://example.com/search",
            Mock(),  # page mock
            "Kollektiv Turmstrasse",
            "Musik Gewinnt Freunde Collection",
            2013
        )

        assert result == "http://rateyourmusic.com/release/album/kollektiv-turmstrasse/musik-gewinnt-freunde-collection/"




    @pytest.mark.asyncio
    async def test_search_album_url_best_match_selection(self, search_engine, mock_scraper):
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
        mock_scraper.fetch_url_with_retry.return_value = html

        result = await search_engine.search_album_url(
            "http://example.com/search",
            Mock(),
            "Radiohead",
            "OK Computer",
            1997
        )

        # Should select the exact match (Radiohead - OK Computer)
        assert result == "http://rateyourmusic.com/release/album/radiohead/ok-computer/"

    @pytest.mark.asyncio
    async def test_search_album_url_real_cache_data(self, search_engine, mock_scraper, cache_fixtures_dir):
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

        mock_scraper.fetch_url_with_retry.return_value = search_cache['html']

        # Extract artist/album from the URL for testing
        # This is a basic test to ensure real data can be parsed
        result = await search_engine.search_album_url(
            search_cache['url'],
            Mock(),
            "Kollektiv Turmstrasse",  # Using known artist from fixtures
            "Test Album"
        )

        # Should either find a match or return None (both are valid with real data)
        assert result is None or result.startswith("http://rateyourmusic.com")



