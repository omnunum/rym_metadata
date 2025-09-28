"""Tests for RYM search engine and text normalization functionality."""

import pytest
from unittest.mock import AsyncMock, Mock
from bs4 import BeautifulSoup
from rym.scraper import RYMScraper





class TestRYMSearchEngine:
    """Test suite for RYM search functionality."""

    @pytest.fixture
    def mock_config(self):
        """Create mock config for scraper."""
        from rym.core import RYMConfig
        return RYMConfig(
            matching_threshold=0.8,
            max_retries=3,
            retry_delay=1.0
        )

    @pytest.fixture
    def scraper(self, mock_config):
        """Create scraper instance with mock config."""
        return RYMScraper(mock_config, None, None, None)

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
        scraper._fetch_url = AsyncMock(return_value=sample_search_html)
        page_mock = Mock()

        result = await scraper._search_album_url(
            "http://example.com/search",
            page_mock,
            "Kollektiv Turmstrasse",
            "Musik Gewinnt Freunde Collection",
            2013
        )

        assert result == "https://rateyourmusic.com/release/album/kollektiv-turmstrasse/musik-gewinnt-freunde-collection/"

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

        # Mock the _fetch_url method
        scraper._fetch_url = AsyncMock(return_value=html)
        page_mock = Mock()

        result = await scraper._search_artist_url(
            "http://example.com/search",
            page_mock,
            "Radiohead"
        )

        assert result == "https://rateyourmusic.com/artist/radiohead"

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

        # Mock the _fetch_url method
        scraper._fetch_url = AsyncMock(return_value=html)
        page_mock = Mock()

        result = await scraper._search_artist_url(
            "http://example.com/search",
            page_mock,
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

        # Mock the _fetch_url method
        scraper._fetch_url = AsyncMock(return_value=html)
        page_mock = Mock()

        result = await scraper._search_artist_url(
            "http://example.com/search",
            page_mock,
            "Radiohead"  # Different case
        )

        assert result == "https://rateyourmusic.com/artist/radiohead"

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

        # Mock the _fetch_url method
        scraper._fetch_url = AsyncMock(return_value=html)
        page_mock = Mock()

        result = await scraper._search_artist_url(
            "http://example.com/search",
            page_mock,
            "Sigur Rós"  # With accent
        )

        assert result == "https://rateyourmusic.com/artist/sigur-ros"

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
        scraper._fetch_url = AsyncMock(return_value=html)
        page_mock = Mock()

        result = await scraper._search_album_url(
            "http://example.com/search",
            page_mock,
            "Radiohead",
            "OK Computer",
            1997
        )

        # Should select the exact match (Radiohead - OK Computer)
        assert result == "https://rateyourmusic.com/release/album/radiohead/ok-computer/"


# Note: Some search-related tests have been removed because the referenced
# methods (_calculate_match_score, fetch_url_with_retry) no longer exist
# after the recent refactoring that simplified the search functionality.