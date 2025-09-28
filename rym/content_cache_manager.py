"""Content-based caching management for RYM scraping."""

import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional, Literal

from .text_utils import normalize_text

# Type alias for content types
ContentType = Literal["artist", "release"]


class ContentCacheManager:
    """Manages content-based caching for RYM pages and artist IDs."""

    def __init__(self, cache_dir: str) -> None:
        self.cache_dir = Path(cache_dir)
        self.logger = logging.getLogger(__name__)

        # Create cache directory if it doesn't exist
        self.cache_dir.mkdir(exist_ok=True)

        # Artist ID cache
        self.artist_id_cache: Dict[str, str] = {}
        self._load_artist_cache()

    def _get_artist_id_cache_file(self) -> Path:
        """Get path to artist ID cache file."""
        return self.cache_dir / "artist_id_cache.json"

    def _build_cache_filename(self, content_type: ContentType, artist: str, album: str = None) -> str:
        """Build filesystem-safe cache filename for content.

        Args:
            content_type: 'artist' or 'release'
            artist: Artist name
            album: Album name (required for release type)

        Returns:
            Safe filename for cache file
        """
        # Normalize artist name for filename
        normalized_artist = normalize_text(
            artist,
            remove_accents=True,
            lowercase=True,
            remove_punctuation=True,
            make_filesystem_safe=True
        )

        if content_type == "artist":
            return f"artist_{normalized_artist}.html"
        elif content_type == "release":
            if not album:
                raise ValueError("Album name required for release content type")
            normalized_album = normalize_text(
                album,
                remove_accents=True,
                lowercase=True,
                remove_parentheticals=True,
                remove_punctuation=True,
                make_filesystem_safe=True
            )
            return f"release_{normalized_artist}_{normalized_album}.html"
        else:
            raise ValueError(f"Unknown content type: {content_type}")

    def get_cached_content(self, content_type: ContentType, artist: str, album: str = None) -> Optional[str]:
        """Get cached HTML content for artist or release.

        Args:
            content_type: 'artist' or 'release'
            artist: Artist name
            album: Album name (required for release type)

        Returns:
            Cached HTML content or None if not found
        """
        try:
            filename = self._build_cache_filename(content_type, artist, album)
            cache_file = self.cache_dir / filename

            if not cache_file.exists():
                self.logger.debug(f"Content cache miss: {content_type} - {artist}" + (f" - {album}" if album else ""))
                return None

            with open(cache_file, 'r', encoding='utf-8') as f:
                html_content = f.read()

            if len(html_content) < 1000:  # Basic validation
                self.logger.warning(f"Cached content too short, removing: {filename}")
                cache_file.unlink()
                return None

            self.logger.info(f"Content cache hit: {content_type} - {artist}" + (f" - {album}" if album else ""))
            return html_content

        except Exception as e:
            self.logger.warning(f"Error reading cached content: {e}")
            return None

    def save_content(self, content_type: ContentType, artist: str, html: str, album: str = None) -> None:
        """Save HTML content to cache.

        Args:
            content_type: 'artist' or 'release'
            artist: Artist name
            html: HTML content to cache
            album: Album name (required for release type)
        """
        try:
            filename = self._build_cache_filename(content_type, artist, album)
            cache_file = self.cache_dir / filename

            with open(cache_file, 'w', encoding='utf-8') as f:
                f.write(html)

            self.logger.debug(f"Cached {content_type} content: {artist}" + (f" - {album}" if album else ""))

        except Exception as e:
            self.logger.error(f"Failed to cache {content_type} content: {e}")

    def _load_artist_cache(self) -> None:
        """Load artist ID cache from file."""
        cache_file = self._get_artist_id_cache_file()

        try:
            if cache_file.exists():
                with open(cache_file, 'r', encoding='utf-8') as f:
                    self.artist_id_cache = json.load(f)
                self.logger.debug(f"Loaded {len(self.artist_id_cache)} artist IDs from cache")
            else:
                self.artist_id_cache = {}
                self.logger.debug("No artist ID cache file found, starting with empty cache")
        except (json.JSONDecodeError, IOError) as e:
            self.logger.warning(f"Error loading artist ID cache: {e}")
            self.artist_id_cache = {}

    def _save_artist_cache(self) -> None:
        """Save artist ID cache to file."""
        cache_file = self._get_artist_id_cache_file()

        try:
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.artist_id_cache, f, ensure_ascii=False, indent=2)
            self.logger.debug(f"Saved {len(self.artist_id_cache)} artist IDs to cache")
        except IOError as e:
            self.logger.error(f"Failed to save artist ID cache: {e}")

    def lookup_artist_id(self, artist_name: str) -> Optional[str]:
        """Look up cached artist ID by name.

        Args:
            artist_name: Artist name to look up

        Returns:
            Artist ID if found, None otherwise
        """
        normalized_name = normalize_text(
            artist_name,
            remove_accents=True,
            lowercase=True
        )

        artist_id = self.artist_id_cache.get(normalized_name)
        if artist_id:
            self.logger.info(f"Artist ID cache hit for: {artist_name}")
        return artist_id

    def save_artist_id(self, artist_name: str, artist_id: str) -> None:
        """Save artist name to ID mapping.

        Args:
            artist_name: Artist name
            artist_id: RYM artist ID
        """
        normalized_name = normalize_text(
            artist_name,
            remove_accents=True,
            lowercase=True
        )

        self.artist_id_cache[normalized_name] = artist_id
        self._save_artist_cache()
        self.logger.debug(f"Cached artist ID for: {artist_name} -> {artist_id}")

    def clear_cache(self) -> int:
        """Clear all cached files.

        Returns:
            Number of files removed
        """
        try:
            # Remove all HTML cache files
            html_files = list(self.cache_dir.glob("*.html"))
            for cache_file in html_files:
                cache_file.unlink()

            # Remove artist ID cache
            artist_cache_file = self._get_artist_id_cache_file()
            if artist_cache_file.exists():
                artist_cache_file.unlink()

            total_removed = len(html_files) + (1 if artist_cache_file.exists() else 0)

            # Clear in-memory cache
            self.artist_id_cache = {}

            self.logger.info(f"Cleared {total_removed} cache files")
            return total_removed

        except Exception as e:
            self.logger.error(f"Error clearing cache: {e}")
            return 0

    def get_cache_info(self) -> Dict[str, Any]:
        """Get cache statistics.

        Returns:
            Dictionary with cache information
        """
        try:
            html_files = list(self.cache_dir.glob("*.html"))
            artist_files = list(self.cache_dir.glob("artist_*.html"))
            release_files = list(self.cache_dir.glob("release_*.html"))

            total_size = sum(f.stat().st_size for f in html_files)
            total_size_mb = total_size / (1024 * 1024)

            return {
                'total_html_files': len(html_files),
                'artist_pages': len(artist_files),
                'release_pages': len(release_files),
                'artist_ids_cached': len(self.artist_id_cache),
                'total_size_mb': round(total_size_mb, 2),
                'cache_dir': str(self.cache_dir)
            }
        except Exception as e:
            self.logger.error(f"Error getting cache info: {e}")
            return {}