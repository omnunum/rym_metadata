"""Tests for RYM scraper integration and genre extraction."""

import pytest
from unittest.mock import AsyncMock, Mock
from rym.scraper import RYMScraper
from rym.cache_manager import HtmlCacheManager
from rym.session_manager import ProxySessionManager
from rym.browser import BrowserManager


class TestRYMScraper:
    """Test suite for RYMScraper integration tests."""

    @pytest.fixture
    def mock_config(self):
        """Create mock config for scraper."""
        config = Mock()
        config_items = {
            'max_retries': 3,
            'retry_delay': 1,
            'page_timeout': 30000,
        }

        def get_item(key):
            item = Mock()
            item.get.return_value = config_items.get(key, 1)
            return item

        config.__getitem__ = get_item
        return config

    @pytest.fixture
    def scraper(self, mock_config, mock_proxy_config):
        """Create scraper instance with mocked dependencies."""
        return RYMScraper(
            config=mock_config,
            proxy_config=mock_proxy_config,
            cache_manager=None,
            session_manager=None,
            browser_manager=None
        )

    @pytest.fixture
    def scraper_with_cache(self, mock_config, mock_proxy_config, temp_cache_dir):
        """Create scraper with cache manager."""
        cache_manager = HtmlCacheManager(str(temp_cache_dir), expiry_days=0)
        return RYMScraper(
            config=mock_config,
            proxy_config=mock_proxy_config,
            cache_manager=cache_manager,
            session_manager=None,
            browser_manager=None
        )

    def test_scraper_initialization(self, scraper, mock_config, mock_proxy_config):
        """Test scraper initializes with correct dependencies."""
        assert scraper.config == mock_config
        assert scraper.proxy_config == mock_proxy_config
        assert scraper.cache_manager is None
        assert scraper.session_manager is None
        assert scraper.browser_manager is None

    def test_extract_genres_from_html_with_genres(self, scraper, sample_album_html):
        """Test genre extraction from sample HTML."""
        result = scraper.extract_genres_from_url.__wrapped__(scraper, "http://test.com", sample_album_html)

        assert 'genres' in result
        assert 'descriptors' in result
        assert 'Electronic' in result['genres']
        assert 'House' in result['genres']
        assert 'Deep House' in result['descriptors']
        assert 'Minimal Techno' in result['descriptors']

    def test_extract_genres_from_html_no_genres(self, scraper):
        """Test genre extraction when no genres are found."""
        html = "<html><body>No genres here</body></html>"

        # Mock the fetch_url_with_retry to return our test HTML
        with patch.object(scraper, 'fetch_url_with_retry', return_value=html):
            result = scraper.extract_genres_from_url.__wrapped__(scraper, "http://test.com", html)

        assert result['genres'] == []
        assert result['descriptors'] == []

    def test_extract_genres_deduplication(self, scraper):
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

        result = scraper.extract_genres_from_url.__wrapped__(scraper, "http://test.com", html)

        assert result['genres'] == ['Electronic', 'House']  # No duplicates

    def test_extract_genres_fallback_search(self, scraper):
        """Test fallback to broader genre search when primary fails."""
        html = '''
        <html>
        <body>
            <a class="genre" href="/genre/rock">Rock</a>
            <a class="genre" href="/genre/alternative">Alternative</a>
        </body>
        </html>
        '''

        result = scraper.extract_genres_from_url.__wrapped__(scraper, "http://test.com", html)

        assert 'Rock' in result['genres']
        assert 'Alternative' in result['genres']

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
        html_content = "<html><body>Fresh content</body></html>"

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

        result = await scraper.fetch_url_with_retry(url, mock_page)

        # Should succeed on second attempt
        assert result == "<html><body>Success</body></html>"
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

        # Mock page to return our sample HTML
        mock_page = Mock()
        mock_page.goto = AsyncMock()
        mock_page.wait_for_load_state = AsyncMock()
        mock_page.content = AsyncMock(return_value=sample_album_html)

        result = await scraper_with_cache.extract_genres_from_url(url, mock_page)

        assert result['genres'] == ['Electronic', 'House']
        assert result['descriptors'] == ['Deep House', 'Minimal Techno']

        # Should be cached
        cached_html = scraper_with_cache.cache_manager.get_cached_html(url)
        assert cached_html == sample_album_html

    @pytest.mark.asyncio
    async def test_process_single_album_direct_url_success(self, scraper_with_cache, sample_album_html):
        """Test processing single album with successful direct URL."""
        # Mock album object
        mock_album = Mock()
        mock_album.albumartist = "Test Artist"
        mock_album.album = "Test Album"

        # Mock page
        mock_page = Mock()
        mock_page.goto = AsyncMock()
        mock_page.wait_for_load_state = AsyncMock()
        mock_page.content = AsyncMock(return_value=sample_album_html)

        result = await scraper_with_cache.process_single_album(mock_album, mock_page, dry_run=True)

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

        # Mock page that fails for direct URL but succeeds for search
        mock_page = Mock()
        call_count = 0

        async def mock_content():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call (direct URL) returns minimal content
                return "<html></html>"
            elif call_count == 2:
                # Second call (search) returns search results
                return sample_search_html
            else:
                # Third call (album page) returns album content
                return sample_album_html

        mock_page.goto = AsyncMock()
        mock_page.wait_for_load_state = AsyncMock()
        mock_page.content = AsyncMock(side_effect=mock_content)

        result = await scraper_with_cache.process_single_album(mock_album, mock_page, dry_run=True)

        assert result is not None
        album, genre_data = result
        assert genre_data['genres'] == ['Electronic', 'House']




