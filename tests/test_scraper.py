"""Tests for RYM scraper integration and genre extraction."""

import pytest
from unittest.mock import AsyncMock, Mock, patch
from rym.scraper import RYMScraper
from rym.cache_manager import HtmlCacheManager
from rym.session_manager import ProxySessionManager
from rym.browser import BrowserManager


class TestRYMScraper:
    """Test suite for RYMScraper integration tests."""

    @pytest.fixture
    def mock_config(self):
        """Create mock config for scraper."""
        from rym.core import RYMConfig
        return RYMConfig(
            max_retries=3,
            retry_delay=1,
            page_timeout=30000,
            matching_threshold=0.8,
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
            session_manager=None,
            browser_manager=None
        )

    @pytest.fixture
    def scraper_with_cache(self, mock_config, temp_cache_dir):
        """Create scraper with cache manager."""
        cache_manager = HtmlCacheManager(str(temp_cache_dir), expiry_days=0)
        return RYMScraper(
            config=mock_config,
            cache_manager=cache_manager,
            session_manager=None,
            browser_manager=None
        )

    def test_scraper_initialization(self, scraper, mock_config):
        """Test scraper initializes with correct dependencies."""
        assert scraper.config == mock_config
        assert scraper.cache_manager is None
        assert scraper.session_manager is None
        assert scraper.browser_manager is None

    @pytest.mark.asyncio
    async def test_extract_genres_from_html_with_genres(self, scraper, sample_album_html):
        """Test genre extraction from sample HTML."""
        # Mock the fetch_url_with_retry to return our test HTML
        with patch.object(scraper, 'fetch_url_with_retry', return_value=sample_album_html):
            result = await scraper.extract_genres_from_url("http://test.com", Mock())

        assert 'genres' in result
        assert 'descriptors' in result
        assert 'Electronic' in result['genres']
        assert 'House' in result['genres']
        assert 'Deep House' in result['descriptors']
        assert 'Minimal Techno' in result['descriptors']

    @pytest.mark.asyncio
    async def test_extract_genres_from_html_no_genres(self, scraper):
        """Test genre extraction when no genres are found."""
        html = "<html><body>No genres here</body></html>"

        # Mock the fetch_url_with_retry to return our test HTML
        with patch.object(scraper, 'fetch_url_with_retry', return_value=html):
            result = await scraper.extract_genres_from_url("http://test.com", Mock())

        assert result['genres'] == []
        assert result['descriptors'] == []

    @pytest.mark.asyncio
    async def test_extract_genres_deduplication(self, scraper):
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

        with patch.object(scraper, 'fetch_url_with_retry', return_value=html):
            result = await scraper.extract_genres_from_url("http://test.com", Mock())

        assert result['genres'] == ['Electronic', 'House']  # No duplicates

    @pytest.mark.asyncio
    async def test_extract_genres_no_fallback_search(self, scraper):
        """Test that random genre links are NOT extracted when proper structure is missing."""
        html = '''
        <html>
        <body>
            <!-- Random genre links outside proper structure should be ignored -->
            <a class="genre" href="/genre/rock">Rock</a>
            <a class="genre" href="/genre/alternative">Alternative</a>
        </body>
        </html>
        '''

        with patch.object(scraper, 'fetch_url_with_retry', return_value=html):
            result = await scraper.extract_genres_from_url("http://test.com", Mock())

        # Should return empty since there's no proper release_genres structure
        assert result['genres'] == []
        assert result['descriptors'] == []

    @pytest.mark.asyncio
    async def test_fetch_url_with_cache_hit(self, scraper_with_cache):
        """Test URL fetching with cache hit."""
        url = "http://test.com"
        cached_html = "<html><body>Cached content</body></html>"

        # Pre-populate cache
        scraper_with_cache.cache_manager.cache_html(url, cached_html)

        # Mock page to avoid actual network call
        mock_page = Mock()

        result = await scraper_with_cache.fetch_url_with_retry(url, mock_page)

        assert result == cached_html
        # Page should not be used since we hit cache
        mock_page.goto.assert_not_called()

    @pytest.mark.asyncio
    async def test_fetch_url_with_cache_miss_and_store(self, scraper_with_cache):
        """Test URL fetching with cache miss and subsequent storage."""
        url = "http://test.com"
        html_content = "<html><body>" + "Fresh content " * 100 + "</body></html>"  # Make it >1000 chars

        # Mock page behavior
        mock_page = Mock()
        mock_page.goto = AsyncMock()
        mock_page.wait_for_load_state = AsyncMock()
        mock_page.content = AsyncMock(return_value=html_content)

        # Mock browser manager methods if they exist
        if scraper_with_cache.browser_manager:
            scraper_with_cache.browser_manager.apply_session_cookies = AsyncMock()
            scraper_with_cache.browser_manager.setup_resource_blocking = AsyncMock()
            scraper_with_cache.browser_manager.solve_cloudflare_challenge = AsyncMock(return_value=False)

        result = await scraper_with_cache.fetch_url_with_retry(url, mock_page)

        assert result == html_content
        # Should be cached now
        cached_result = scraper_with_cache.cache_manager.get_cached_html(url)
        assert cached_result == html_content

    @pytest.mark.asyncio
    async def test_fetch_url_retry_logic(self, scraper):
        """Test retry logic when page loading fails."""
        url = "http://test.com"

        # Mock page that fails initially then succeeds
        mock_page = Mock()
        mock_page.goto = AsyncMock(side_effect=[Exception("Network error"), None])
        mock_page.wait_for_load_state = AsyncMock()
        mock_page.content = AsyncMock(return_value="<html><body>Success</body></html>")

        # Update the content to be longer than 1000 chars
        success_content = "<html><body>" + "Success " * 150 + "</body></html>"
        mock_page.content = AsyncMock(return_value=success_content)

        result = await scraper.fetch_url_with_retry(url, mock_page)

        # Should succeed on second attempt
        assert result == success_content
        assert mock_page.goto.call_count == 2

    @pytest.mark.asyncio
    async def test_fetch_url_max_retries_exceeded(self, scraper):
        """Test behavior when max retries are exceeded."""
        url = "http://test.com"

        # Mock page that always fails
        mock_page = Mock()
        mock_page.goto = AsyncMock(side_effect=Exception("Persistent error"))

        result = await scraper.fetch_url_with_retry(url, mock_page)

        assert result is None
        # Should try max_retries + 1 times (initial + retries)
        assert mock_page.goto.call_count == 4  # 3 retries + 1 initial

    @pytest.mark.asyncio
    async def test_fetch_url_minimal_content_rejection(self, scraper):
        """Test rejection of responses with minimal content."""
        url = "http://test.com"
        minimal_html = "<html></html>"  # Less than 1000 chars

        mock_page = Mock()
        mock_page.goto = AsyncMock()
        mock_page.wait_for_load_state = AsyncMock()
        mock_page.content = AsyncMock(return_value=minimal_html)

        result = await scraper.fetch_url_with_retry(url, mock_page)

        # Should return None for minimal content (likely blocked)
        assert result is None

    @pytest.mark.asyncio
    async def test_extract_genres_from_url_integration(self, scraper_with_cache, sample_album_html):
        """Test full genre extraction integration with caching."""
        url = "http://test.com/album"

        # Mock the fetch_url_with_retry method to return our sample HTML
        with patch.object(scraper_with_cache, 'fetch_url_with_retry', return_value=sample_album_html):
            result = await scraper_with_cache.extract_genres_from_url(url, Mock())

        assert result['genres'] == ['Electronic', 'House']
        assert result['descriptors'] == ['Deep House', 'Minimal Techno']

    @pytest.mark.asyncio
    async def test_process_single_album_direct_url_success(self, scraper_with_cache, sample_album_html):
        """Test processing single album with successful direct URL."""
        # Mock album object
        mock_album = Mock()
        mock_album.albumartist = "Test Artist"
        mock_album.album = "Test Album"
        mock_album.year = 2020  # Add year as integer

        # Mock the extract_genres_from_url method to return expected data
        with patch.object(scraper_with_cache, 'extract_genres_from_url', return_value={'genres': ['Electronic', 'House'], 'descriptors': ['Deep House', 'Minimal Techno']}):
            result = await scraper_with_cache.process_single_album(mock_album, Mock(), dry_run=True)

        assert result is not None
        album, genre_data = result
        assert genre_data['genres'] == ['Electronic', 'House']
        assert genre_data['descriptors'] == ['Deep House', 'Minimal Techno']

    @pytest.mark.asyncio
    async def test_process_single_album_search_fallback(self, scraper_with_cache, sample_search_html, sample_album_html):
        """Test processing single album with search fallback."""
        # Mock album object
        mock_album = Mock()
        mock_album.albumartist = "Kollektiv Turmstrasse"
        mock_album.album = "Musik Gewinnt Freunde Collection"
        mock_album.year = 2013

        # Mock the get_album_genres_and_descriptors method to simulate search fallback working
        expected_data = {'genres': ['Electronic', 'House'], 'descriptors': ['Deep House', 'Minimal Techno']}
        with patch.object(scraper_with_cache, 'get_album_genres_and_descriptors', return_value=expected_data):
            result = await scraper_with_cache.process_single_album(mock_album, Mock(), dry_run=True)

        assert result is not None
        album, genre_data = result
        assert genre_data['genres'] == ['Electronic', 'House']




