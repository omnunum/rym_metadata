"""Tests for content cache manager functionality."""

import json
from pathlib import Path
from rym.content_cache_manager import ContentCacheManager
from rym.text_utils import normalize_text


class TestContentCacheManager:
    """Test suite for ContentCacheManager."""

    def test_cache_creation(self, temp_cache_dir):
        """Test cache manager creates directory if it doesn't exist."""
        cache_manager = ContentCacheManager(str(temp_cache_dir))
        assert temp_cache_dir.exists()
        assert cache_manager.cache_dir == temp_cache_dir


    def test_content_cache_artist(self, cache_manager):
        """Test caching and retrieving artist content."""
        artist = "Test Artist"
        # Make HTML content long enough to pass validation (>1000 chars)
        html_content = "<html><body>Artist page content" + "x" * 1000 + "</body></html>"

        # Cache artist content
        cache_manager.save_content("artist", artist, html_content)

        # Retrieve from cache
        cached_html = cache_manager.get_cached_content("artist", artist)
        assert cached_html == html_content

    def test_content_cache_release(self, cache_manager):
        """Test caching and retrieving release content."""
        artist = "Test Artist"
        album = "Test Album (2023 Remaster)"
        # Make HTML content long enough to pass validation (>1000 chars)
        html_content = "<html><body>Release page content" + "x" * 1000 + "</body></html>"

        # Cache release content
        cache_manager.save_content("release", artist, html_content, album)

        # Retrieve from cache
        cached_html = cache_manager.get_cached_content("release", artist, album)
        assert cached_html == html_content

    def test_content_cache_miss(self, cache_manager):
        """Test cache miss returns None."""
        # Try to get non-existent content
        cached_html = cache_manager.get_cached_content("artist", "Nonexistent Artist")
        assert cached_html is None

        cached_html = cache_manager.get_cached_content("release", "Artist", "Album")
        assert cached_html is None

    def test_artist_id_cache(self, cache_manager):
        """Test artist ID caching functionality."""
        artist_name = "Test Artist"
        artist_id = "12345"

        # Initially no cached ID
        cached_id = cache_manager.lookup_artist_id(artist_name)
        assert cached_id is None

        # Save artist ID
        cache_manager.save_artist_id(artist_name, artist_id)

        # Retrieve cached ID
        cached_id = cache_manager.lookup_artist_id(artist_name)
        assert cached_id == artist_id

    def test_artist_id_cache_normalization(self, cache_manager):
        """Test artist ID cache uses normalized names."""
        # Save with accents
        cache_manager.save_artist_id("Café Tacvba", "12345")

        # Lookup without accents should work
        cached_id = cache_manager.lookup_artist_id("Cafe Tacvba")
        assert cached_id == "12345"

        # Lookup with different casing should work
        cached_id = cache_manager.lookup_artist_id("CAFE TACVBA")
        assert cached_id == "12345"


    def test_clear_cache(self, cache_manager):
        """Test clearing all cache files."""
        # Add some content
        cache_manager.save_content("artist", "Artist 1", "<html>content1</html>")
        cache_manager.save_content("release", "Artist 2", "<html>content2</html>", "Album 1")
        cache_manager.save_artist_id("Artist 3", "12345")

        # Clear cache
        removed_count = cache_manager.clear_cache()
        assert removed_count > 0

        # Verify content is gone
        assert cache_manager.get_cached_content("artist", "Artist 1") is None
        assert cache_manager.get_cached_content("release", "Artist 2", "Album 1") is None
        assert cache_manager.lookup_artist_id("Artist 3") is None

    def test_cache_info(self, cache_manager):
        """Test cache statistics."""
        # Add some content
        cache_manager.save_content("artist", "Artist 1", "<html>content1</html>")
        cache_manager.save_content("release", "Artist 2", "<html>content2</html>", "Album 1")
        cache_manager.save_artist_id("Artist 3", "12345")

        # Get cache info
        info = cache_manager.get_cache_info()
        assert info['total_html_files'] == 2
        assert info['artist_pages'] == 1
        assert info['release_pages'] == 1
        assert info['artist_ids_cached'] == 1
        assert 'total_size_mb' in info
        assert 'cache_dir' in info

