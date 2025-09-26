"""Tests for content cache manager functionality."""

import json
from pathlib import Path
from rym.content_cache_manager import ContentCacheManager


class TestContentCacheManager:
    """Test suite for ContentCacheManager."""

    def test_cache_creation(self, temp_cache_dir):
        """Test cache manager creates directory if it doesn't exist."""
        cache_manager = ContentCacheManager(str(temp_cache_dir))
        assert temp_cache_dir.exists()
        assert cache_manager.cache_dir == temp_cache_dir

    def test_normalize_text_basic(self):
        """Test basic text normalization."""
        # Test remove_accents
        result = ContentCacheManager.normalize_text("Café", remove_accents=True, lowercase=False)
        assert result == "Cafe"

        # Test lowercase
        result = ContentCacheManager.normalize_text("HELLO", lowercase=True, remove_accents=False)
        assert result == "hello"

        # Test remove_parentheticals
        result = ContentCacheManager.normalize_text("Album (2023 Remaster)", remove_parentheticals=True)
        assert result == "album"

        # Test remove_punctuation
        result = ContentCacheManager.normalize_text("Hello, World!", remove_punctuation=True, lowercase=False)
        assert result == "Hello World"

        # Test filesystem safe
        result = ContentCacheManager.normalize_text("file<>name", make_filesystem_safe=True, lowercase=False)
        assert result == "file__name"

    def test_normalize_text_combined(self):
        """Test combined normalization features."""
        text = "Café Tacvba - El Baile y el Salón (2023 Remaster)"
        result = ContentCacheManager.normalize_text(
            text,
            remove_accents=True,
            lowercase=True,
            remove_parentheticals=True,
            remove_punctuation=True,
            make_filesystem_safe=True
        )
        # Should remove accents, lowercase, remove parentheticals, remove punctuation, make filesystem safe
        expected = "cafe_tacvba_el_baile_y_el_salon"
        assert result == expected

    def test_content_cache_artist(self, cache_manager):
        """Test caching and retrieving artist content."""
        artist = "Test Artist"
        html_content = "<html><body>Artist page content</body></html>"

        # Cache artist content
        cache_manager.save_content("artist", artist, html_content)

        # Retrieve from cache
        cached_html = cache_manager.get_cached_content("artist", artist)
        assert cached_html == html_content

    def test_content_cache_release(self, cache_manager):
        """Test caching and retrieving release content."""
        artist = "Test Artist"
        album = "Test Album (2023 Remaster)"
        html_content = "<html><body>Release page content</body></html>"

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

        cached_html = cache_manager.get_cached_content("release", "Artist", None, "Album")
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

    def test_cache_filename_generation(self, cache_manager):
        """Test cache filename generation."""
        # Test artist filename
        filename = cache_manager._build_cache_filename("artist", "Test Artist")
        assert filename == "artist_test_artist.html"

        # Test release filename
        filename = cache_manager._build_cache_filename("release", "Test Artist", "Test Album (2023)")
        assert filename == "release_test_artist_test_album.html"

        # Test with special characters
        filename = cache_manager._build_cache_filename("artist", "Café Tacvba")
        assert filename == "artist_cafe_tacvba.html"

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

    def test_invalid_content_type_raises_error(self, cache_manager):
        """Test that invalid content type raises ValueError."""
        try:
            cache_manager._build_cache_filename("invalid", "artist")
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "Unknown content type" in str(e)

    def test_release_without_album_raises_error(self, cache_manager):
        """Test that release content type without album raises ValueError."""
        try:
            cache_manager._build_cache_filename("release", "artist")
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "Album name required" in str(e)

    def test_minimal_content_rejection(self, cache_manager):
        """Test that cached content under 1000 chars is rejected."""
        artist = "Test Artist"
        minimal_html = "<html></html>"  # Less than 1000 chars

        # Cache minimal content
        cache_manager.save_content("artist", artist, minimal_html)

        # Should return None due to size validation
        cached_html = cache_manager.get_cached_content("artist", artist)
        assert cached_html is None