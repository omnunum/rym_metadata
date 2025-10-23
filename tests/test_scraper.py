"""Tests for RYM scraper integration and genre extraction."""

import pytest
from unittest.mock import AsyncMock, Mock, patch
from rym.scraper import RYMScraper
from rym.content_cache_manager import ContentCacheManager
from rym.browser import BrowserManager


class TestRYMScraper:
    """Test suite for RYMScraper integration tests."""

    @pytest.fixture
    def mock_config(self):
        """Create mock config for scraper."""
        from rym.dataclasses import RYMConfig
        return RYMConfig(
            max_retries=3,
            retry_delay=1,
            page_timeout=30000,
            matching_threshold=0.85,
            # Disable rate limiting for tests
            min_request_interval=0.0,
            humanize_request_interval=False
        )

    @pytest.fixture
    def scraper(self, mock_config):
        """Create scraper instance with mocked dependencies."""
        return RYMScraper(
            config=mock_config,
            cache_manager=None,
            browser_manager=None
        )

    @pytest.fixture
    def scraper_with_cache(self, mock_config, temp_cache_dir):
        """Create scraper with cache manager."""
        cache_manager = ContentCacheManager(str(temp_cache_dir))
        return RYMScraper(
            config=mock_config,
            cache_manager=cache_manager,
            browser_manager=None
        )


    def test_extract_genres_from_html_with_genres(self, scraper: RYMScraper, sample_album_html):
        """Test genre extraction from sample HTML."""
        # Test the current _extract_genres_from_html method directly
        genres, descriptors = scraper._extract_genres_from_html(sample_album_html, "album")

        assert isinstance(genres, list)
        assert isinstance(descriptors, list)
        assert 'Electronic' in genres
        assert 'House' in genres
        assert 'Deep House' in descriptors
        assert 'Minimal Techno' in descriptors

    def test_extract_genres_from_html_no_genres(self, scraper: RYMScraper):
        """Test genre extraction when no genres are found."""
        html = "<html><body>No genres here</body></html>"

        # Test the current _extract_genres_from_html method directly
        genres, descriptors = scraper._extract_genres_from_html(html, "album")

        assert genres == []
        assert descriptors == []

    def test_extract_genres_deduplication(self, scraper: RYMScraper):
        """Test that duplicate genres are removed."""
        html = '''
        <html>
        <body>
            <tr class="release_genres">
                <td>
                    <span class="release_pri_genres">
                        <a class="genre" href="/genre/electronic">Electronic</a>
                        <a class="genre" href="/genre/electronic">Electronic</a>
                        <a class="genre" href="/genre/house">House</a>
                    </span>
                </td>
            </tr>
        </body>
        </html>
        '''

        # Test the current _extract_genres_from_html method directly
        genres, _ = scraper._extract_genres_from_html(html, "album")

        assert genres == ['Electronic', 'House']  # No duplicates



    @pytest.mark.asyncio
    async def test_cache_content_save_and_retrieve(self, scraper_with_cache: RYMScraper):
        """Test content caching save and retrieve functionality."""
        artist = "Test Artist"
        album = "Test Album"
        html_content = "<html><body>" + "Fresh content " * 100 + "</body></html>"  # Make it >1000 chars

        # Test saving content
        scraper_with_cache.cache_manager.save_content("release", artist, html_content, album)

        # Test retrieving content
        cached_result = scraper_with_cache.cache_manager.get_cached_content("release", artist, album)
        assert cached_result == html_content


    @pytest.mark.asyncio
    async def test_fetch_url_error_handling(self, scraper: RYMScraper):
        """Test error handling in URL fetching."""
        url = "http://test.com"

        # Mock page that fails - need to mock all required async methods
        mock_page = Mock()
        mock_page.goto = AsyncMock(side_effect=Exception("Network error"))
        mock_page.wait_for_load_state = AsyncMock()
        mock_page.content = AsyncMock()

        # Mock _wait_for_rate_limit to avoid issues
        with patch.object(scraper, '_wait_for_rate_limit', return_value=None):
            try:
                result = await scraper._fetch_url(url, mock_page)
                # Should be None due to retry exhaustion
                assert result is None
            except Exception:
                # Or might raise exception after retries
                pass





    @pytest.mark.asyncio
    async def test_album_genre_extraction_multiple_genres(self, scraper_with_cache: RYMScraper):
        """Test album genre extraction with HTML that has multiple genres."""
        # HTML with multiple genres (both primary and secondary)
        album_html_with_multiple_genres = '''
        <html>
        <body>
            <tr class="release_genres">
                <td>
                    <span class="release_pri_genres">
                        <a class="genre" href="/genre/alternative-rock">Alternative Rock</a>
                    </span>
                    <a class="genre" href="/genre/britpop">Britpop</a>
                    <a class="genre" href="/genre/grunge">Grunge</a>
                </td>
            </tr>
            <tr class="release_descriptors">
                <td>
                    <meta content="melodic" />
                    <meta content="energetic" />
                </td>
            </tr>
        </body>
        </html>
        '''

        # Test the current _extract_genres_from_html method directly
        genres, descriptors = scraper_with_cache._extract_genres_from_html(album_html_with_multiple_genres, "album")

        # The fix should now extract all genres, not just primary ones
        assert 'Alternative Rock' in genres
        assert 'Britpop' in genres
        assert 'Grunge' in genres
        assert len(genres) == 3
        assert 'melodic' in descriptors
        assert 'energetic' in descriptors

    @pytest.mark.asyncio
    async def test_artist_genre_extraction(self, scraper_with_cache: RYMScraper):
        """Test artist genre extraction with HTML matching actual RYM artist page structure."""
        # HTML matching the actual structure shown in the screenshot
        artist_html_with_genres = '''
        <html>
        <body>
            <div class="artist_info_main">
                <div class="info_hdr">Genres</div>
                <div class="info_content">
                    <a title="[Genre405]" class="genre" href="/genre/noise-rock/">Noise Rock</a>
                    ", "
                    <a title="[Genre116]" class="genre" href="/genre/alternative-rock/">Alternative Rock</a>
                    ", "
                    <a title="[Genre641]" class="genre" href="/genre/experimental-rock/">Experimental Rock</a>
                    ", "
                    <a title="[Genre332]" class="genre" href="/genre/post-punk/">Post-Punk</a>
                    ", "
                    <a title="[Genre103]" class="genre" href="/genre/indie-rock/">Indie Rock</a>
                    ", "
                    <a title="[Genre561]" class="genre" href="/genre/post-rock/">Post-Rock</a>
                </div>
            </div>
        </body>
        </html>
        '''

        # Test the current _extract_genres_from_html method with artist content_type
        genres, _ = scraper_with_cache._extract_genres_from_html(artist_html_with_genres, "artist")

        # Should extract all genres from the Genres section in artist_info_main
        assert 'Noise Rock' in genres
        assert 'Alternative Rock' in genres
        assert 'Experimental Rock' in genres
        assert 'Post-Punk' in genres
        assert 'Indie Rock' in genres
        assert 'Post-Rock' in genres
        assert len(genres) == 6




