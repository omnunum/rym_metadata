"""Core RYM metadata scraping functionality independent of beets.

This module provides a clean interface for scraping RateYourMusic metadata
that can be used standalone or integrated into other tools like streamrip.
"""

import logging
from pathlib import Path
from typing import Dict, Optional, Any, Literal

from rym.dataclasses import RYMMetadata, RYMConfig
from rym.session_manager import ProxySessionManager
from rym.content_cache_manager import ContentCacheManager
from rym.browser import BrowserManager
from rym.scraper import RYMScraper

class RYMMetadataScraper:
    """Standalone RYM metadata scraper for use in any application."""

    def __init__(self, config: Optional[RYMConfig] = None) -> None:
        self.config = config or RYMConfig()  # Use defaults if no config provided
        self.logger = logging.getLogger(__name__)

        # Initialize components
        self._init_session_manager()
        self._init_cache_manager()
        self._init_browser_manager()
        self._init_scraper()


    def _init_session_manager(self) -> None:
        """Initialize proxy session manager."""
        self.session_manager = None
        if self.config.proxy_enabled and self.config.has_proxy_server:
            self.session_manager = ProxySessionManager(self.config, self.config.session_state_file_path)

    def _init_cache_manager(self) -> None:
        """Initialize content cache manager."""
        self.cache_manager = None
        if self.config.cache_enabled:
            # Make cache_dir absolute if it's not already
            cache_dir = Path(self.config.cache_dir)
            if not cache_dir.is_absolute():
                cache_dir = cache_dir.resolve()

            self.cache_manager = ContentCacheManager(str(cache_dir))

    def _init_browser_manager(self) -> None:
        """Initialize browser manager."""
        self.browser_manager = BrowserManager(self.config, self.session_manager)

    def _init_scraper(self) -> None:
        """Initialize RYM scraper."""
        self.scraper = RYMScraper(
            self.config,
            self.cache_manager,
            self.browser_manager
        )

    async def __aenter__(self):
        """Async context manager entry - start browser session."""
        await self.scraper.__aenter__()
        return self

    async def __aexit__(self, _exc_type, _exc_val, _exc_tb):
        """Async context manager exit - cleanup browser session."""
        await self.scraper.__aexit__(_exc_type, _exc_val, _exc_tb)


    async def get_album_metadata(self, artist: str, album: str, year: Optional[int] = None, album_type: Literal["album", "single", "ep", "compilation"] = "album") -> Optional[RYMMetadata]:
        """Get metadata for a single album.

        Args:
            artist: Artist name
            album: Album name
            year: Optional album year for better search matching
            album_type: Type of release ("album", "single", "ep", "compilation")

        Returns:
            RYMMetadata object or None if not found
        """
        try:
            # Get album metadata, with artist fallback
            scraper_result = await self.scraper.get_album_metadata(artist, album, year, album_type)

            if not scraper_result or not scraper_result.genres:
                return None

            # Build URL for reference (try direct first)
            url = self.scraper.build_direct_url(artist, album, album_type)

            return RYMMetadata(
                artist=artist,
                album=album,
                genres=scraper_result.genres,
                descriptors=scraper_result.descriptors,
                url=url,
                album_type=album_type,
                release_date=scraper_result.release_date
            )

        except Exception as e:
            self.logger.error(f"Error getting metadata for {artist} - {album}: {e}")
            return None

    async def get_artist_metadata(self, artist: str) -> Optional[RYMMetadata]:
        """Get metadata for a single artist.

        Args:
            artist: Artist name

        Returns:
            RYMMetadata object or None if not found
        """
        try:
            # Get artist metadata directly
            scraper_result = await self.scraper.get_artist_metadata(artist)

            if not scraper_result or not scraper_result.genres:
                return None

            # Build URL for reference (try direct first)
            url = self.scraper.build_artist_url(artist)

            return RYMMetadata(
                artist=artist,
                genres=scraper_result.genres,
                descriptors=scraper_result.descriptors,
                url=url,
                album=None,
                album_type=None,
                release_date=scraper_result.release_date
            )

        except Exception as e:
            self.logger.error(f"Error getting metadata for artist {artist}: {e}")
            return None


    def clear_cache(self) -> int:
        """Clear HTML cache and return number of files cleared."""
        if self.cache_manager:
            return self.cache_manager.clear_cache()
        return 0

    def get_cache_info(self) -> Dict[str, Any]:
        """Get cache statistics."""
        if self.cache_manager:
            return self.cache_manager.get_cache_info()
        return {'cache_enabled': False}





