"""Tests for HTML cache manager functionality."""

import json
from datetime import datetime, timedelta
from rym.cache_manager import HtmlCacheManager


class TestHtmlCacheManager:
    """Test suite for HtmlCacheManager."""

    def test_cache_creation(self, temp_cache_dir):
        """Test cache manager creates directory if it doesn't exist."""
        cache_manager = HtmlCacheManager(str(temp_cache_dir), expiry_days=0)
        assert temp_cache_dir.exists()
        assert cache_manager.cache_dir == temp_cache_dir
        assert cache_manager.expiry_days == 0



    def test_cache_html_and_retrieval(self, cache_manager):
        """Test caching HTML and retrieving it."""
        url = "http://example.com/test"
        html_content = "<html><body>Test content</body></html>"

        # Cache HTML
        cache_manager.cache_html(url, html_content)

        # Retrieve from cache
        cached_html = cache_manager.get_cached_html(url)
        assert cached_html == html_content

    def test_cache_miss(self, cache_manager):
        """Test cache miss returns None."""
        url = "http://example.com/nonexistent"
        cached_html = cache_manager.get_cached_html(url)
        assert cached_html is None



    def test_cache_expiry_enabled(self, temp_cache_dir):
        """Test cache expiry when enabled."""
        cache_manager = HtmlCacheManager(str(temp_cache_dir), expiry_days=1)
        url = "http://example.com/test"
        html_content = "<html><body>Test content</body></html>"

        # Manually create expired cache file
        cache_file = cache_manager._get_cache_file(url)
        expired_time = datetime.now() - timedelta(days=2)
        cache_data = {
            'url': url,
            'html': html_content,
            'timestamp': expired_time.isoformat(),
            'expires': (expired_time + timedelta(days=1)).isoformat()
        }

        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f)

        # Should return None for expired cache and remove file
        cached_html = cache_manager.get_cached_html(url)
        assert cached_html is None
        assert not cache_file.exists()

    def test_corrupted_cache_handling(self, cache_manager):
        """Test handling of corrupted cache files."""
        url = "http://example.com/test"
        cache_file = cache_manager._get_cache_file(url)

        # Create corrupted JSON file
        with open(cache_file, 'w', encoding='utf-8') as f:
            f.write("invalid json content")

        # Should return None and remove corrupted file
        cached_html = cache_manager.get_cached_html(url)
        assert cached_html is None
        assert not cache_file.exists()

    def test_clear_cache(self, cache_manager):
        """Test clearing all cached files."""
        urls = [
            "http://example.com/test1",
            "http://example.com/test2",
            "http://example.com/test3"
        ]
        html_content = "<html><body>Test content</body></html>"

        # Cache multiple files
        for url in urls:
            cache_manager.cache_html(url, html_content)

        # Verify files exist
        assert len(list(cache_manager.cache_dir.glob("*.json"))) == 3

        # Clear cache
        cleared_count = cache_manager.clear_cache()
        assert cleared_count == 3
        assert len(list(cache_manager.cache_dir.glob("*.json"))) == 0


    def test_cleanup_expired(self, temp_cache_dir):
        """Test cleanup of expired cache files."""
        cache_manager = HtmlCacheManager(str(temp_cache_dir), expiry_days=1)

        # Create a mix of valid and expired cache files
        valid_url = "http://example.com/valid"
        expired_url = "http://example.com/expired"
        html_content = "<html><body>Test content</body></html>"

        # Valid cache
        cache_manager.cache_html(valid_url, html_content)

        # Expired cache (manually created)
        expired_cache_file = cache_manager._get_cache_file(expired_url)
        expired_time = datetime.now() - timedelta(days=2)
        cache_data = {
            'url': expired_url,
            'html': html_content,
            'timestamp': expired_time.isoformat(),
            'expires': (expired_time + timedelta(days=1)).isoformat()
        }

        with open(expired_cache_file, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f)

        # Cleanup should remove only expired file
        removed_count = cache_manager.cleanup_expired()
        assert removed_count == 1

        # Valid cache should still exist
        assert cache_manager.get_cached_html(valid_url) == html_content
        assert cache_manager.get_cached_html(expired_url) is None

