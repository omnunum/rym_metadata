"""Tests for RYM search engine and text normalization functionality."""

import pytest
from unittest.mock import AsyncMock, Mock
from bs4 import BeautifulSoup
from rym.dataclasses import DiscographyCandidate
from rym.scraper import RYMScraper





class TestRYMSearchEngine:
    """Test suite for RYM search functionality."""

    @pytest.fixture
    def mock_config(self):
        """Create mock config for scraper."""
        from rym.dataclasses import RYMConfig
        return RYMConfig(
            matching_threshold=0.8,
            max_retries=3,
            retry_delay=1.0
        )

    @pytest.fixture
    def scraper(self, mock_config):
        """Create scraper instance with mock config."""
        mock_browser_manager = Mock()
        return RYMScraper(mock_config, None, mock_browser_manager)

    def test_score_discography_candidate_case_insensitive(self, scraper):
        """Test discography candidate scoring is case insensitive."""
        candidate = DiscographyCandidate(
            album="ok computer",
            year=1997,
            url="/release/album/radiohead/ok-computer/"
        )
        score = scraper._score_discography_candidate(candidate, "OK COMPUTER", 1997)
        assert score == 1.0

    def test_score_discography_candidate_year_scoring(self, scraper):
        """Test year scoring with different year differences."""
        candidate = DiscographyCandidate(
            album="Test Album",
            year=2000,
            url="/release/album/test/test-album/"
        )

        # Exact year match
        score = scraper._score_discography_candidate(candidate, "Test Album", 2000)
        assert score == 1.0

        # 1 year difference - should get 0.9 year score
        score = scraper._score_discography_candidate(candidate, "Test Album", 2001)
        expected = 1.0 * 0.8 + 0.9 * 0.2  # album_score * 0.8 + year_score * 0.2
        assert score == expected

        # Large year difference - should get 0.5 year score
        score = scraper._score_discography_candidate(candidate, "Test Album", 2010)
        expected = 1.0 * 0.8 + 0.5 * 0.2
        assert score == expected

    def test_score_discography_candidate_no_year(self, scraper):
        """Test scoring when no year information is available."""
        candidate = DiscographyCandidate(
            album="Test Album",
            year=None,
            url="/release/album/test/test-album/"
        )
        score = scraper._score_discography_candidate(candidate, "Test Album", 2000)
        # Should use default year score of 1.0
        assert score == 1.0

    def test_score_discography_candidates_best_match(self, scraper):
        """Test that best scoring candidate is selected."""
        candidates = [
            DiscographyCandidate("Different Album", 2000, "/release/different/"),
            DiscographyCandidate("OK Computer", 1997, "/release/ok-computer/"),
            DiscographyCandidate("Another Album", 1995, "/release/another/")
        ]

        # Mock the config matching threshold
        scraper.config.matching_threshold = 0.8

        result = scraper._score_discography_candidates(candidates, "OK Computer", 1997)
        assert result == "https://rateyourmusic.com/release/ok-computer/"

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

        # Mock the navigate_page_with_rate_limiting method
        scraper.navigate_page_with_rate_limiting = AsyncMock(return_value=html)
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

        # Mock the navigate_page_with_rate_limiting method
        scraper.navigate_page_with_rate_limiting = AsyncMock(return_value=html)
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

        # Mock the navigate_page_with_rate_limiting method
        scraper.navigate_page_with_rate_limiting = AsyncMock(return_value=html)
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

        # Mock the navigate_page_with_rate_limiting method
        scraper.navigate_page_with_rate_limiting = AsyncMock(return_value=html)
        page_mock = Mock()

        result = await scraper._search_artist_url(
            "http://example.com/search",
            page_mock,
            "Sigur Rós"  # With accent
        )

        assert result == "https://rateyourmusic.com/artist/sigur-ros"



# Note: Some search-related tests have been removed because the referenced
# methods (_calculate_match_score, fetch_url_with_retry) no longer exist
# after the recent refactoring that simplified the search functionality.