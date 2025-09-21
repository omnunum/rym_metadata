"""Core RYM metadata scraping functionality independent of beets.

This module provides a clean interface for scraping RateYourMusic metadata
that can be used standalone or integrated into other tools like streamrip.
"""

import asyncio
import logging
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass

from .session_manager import ProxySessionManager
from .cache_manager import HtmlCacheManager
from .browser import BrowserManager
from .scraper import RYMScraper


@dataclass
class AlbumMetadata:
    """Container for album metadata extracted from RYM."""
    artist: str
    album: str
    genres: List[str]
    descriptors: List[str]
    url: Optional[str] = None


@dataclass
class RYMConfig:
    """Configuration for standalone RYM scraper."""
    # Proxy configuration
    proxy_enabled: bool = True
    proxy_host: Optional[str] = None
    proxy_port: Optional[int] = None
    proxy_username: Optional[str] = None
    proxy_password: Optional[str] = None
    proxy_use_tls: bool = False
    proxy_cert_path: Optional[str] = None

    # Session management
    session_type: str = 'sticky'  # 'sticky', 'rotate', 'const', 'none'
    session_duration: int = 600
    session_id_length: int = 10
    port_range_start: int = 10001
    port_range_end: int = 10100

    # Browser and retry settings
    max_retries: int = 3
    retry_delay: float = 2.0
    page_timeout: int = 30000

    # Cache settings
    cache_enabled: bool = True
    cache_dir: str = '.rym_cache'
    cache_expiry_days: int = 0

    # Resource blocking
    resource_blocking_enabled: bool = True

    # Search matching
    matching_threshold: float = 0.8  # Minimum similarity score (0.0-1.0) for accepting matches

    @classmethod
    def from_beets_config(cls, config) -> 'RYMConfig':
        """Create RYMConfig from beets configuration object."""
        return cls(
            # Proxy configuration
            proxy_enabled=config['proxy_enabled'].get(),
            proxy_host=config['proxy_host'].get(),
            proxy_port=config['proxy_port'].get(),
            proxy_username=config['proxy_username'].get(),
            proxy_password=config['proxy_password'].get(),
            proxy_use_tls=config['proxy_use_tls'].get(False),
            proxy_cert_path=config['proxy_cert_path'].get(),

            # Session management
            session_type=config['session_type'].get('none'),
            session_duration=config['session_duration'].get(600),
            session_id_length=config['session_id_length'].get(10),
            port_range_start=config['port_range_start'].get(10001),
            port_range_end=config['port_range_end'].get(10100),

            # Browser and retry settings
            max_retries=config['max_retries'].get(3),
            retry_delay=config['retry_delay'].get(2.0),
            page_timeout=config['page_timeout'].get(30000),

            # Cache settings
            cache_enabled=config['cache_enabled'].get(True),
            cache_dir=config['cache_dir'].get('.rym_cache'),
            cache_expiry_days=config['cache_expiry_days'].get(0),

            # Resource blocking
            resource_blocking_enabled=config['resource_blocking_enabled'].get(True),

            # Search matching
            matching_threshold=config['matching_threshold'].get(0.8),
        )

    @property
    def server_url(self) -> Optional[str]:
        """Build complete proxy server URL with protocol."""
        if not (self.proxy_host and self.proxy_port):
            return None
        protocol = "https" if self.proxy_use_tls else "http"
        return f"{protocol}://{self.proxy_host}:{self.proxy_port}"

    @property
    def is_proxy_valid(self) -> bool:
        """Check if proxy configuration is complete."""
        return (self.proxy_enabled and
                self.proxy_host is not None and
                self.proxy_port is not None and
                self.proxy_username is not None and
                self.proxy_password is not None)

    @property
    def has_proxy_credentials(self) -> bool:
        """Check if proxy username and password are provided."""
        return self.proxy_username is not None and self.proxy_password is not None

    @property
    def has_proxy_server(self) -> bool:
        """Check if proxy host and port are provided."""
        return self.proxy_host is not None and self.proxy_port is not None



class RYMMetadataScraper:
    """Standalone RYM metadata scraper for use in any application."""

    def __init__(self, config: RYMConfig) -> None:
        self.config = config
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
            self.session_manager = ProxySessionManager(self.config)

    def _init_cache_manager(self) -> None:
        """Initialize HTML cache manager."""
        self.cache_manager = None
        if self.config.cache_enabled:
            self.cache_manager = HtmlCacheManager(
                self.config.cache_dir,
                self.config.cache_expiry_days
            )
            # Clean up expired cache on startup
            if self.config.cache_expiry_days > 0:
                self.cache_manager.cleanup_expired()

    def _init_browser_manager(self) -> None:
        """Initialize browser manager."""
        self.browser_manager = BrowserManager(self.config, self.session_manager)

    def _init_scraper(self) -> None:
        """Initialize RYM scraper."""
        self.scraper = RYMScraper(
            self.config,
            self.cache_manager,
            self.session_manager,
            self.browser_manager
        )

    async def get_album_metadata(self, artist: str, album: str, year: Optional[int] = None) -> Optional[AlbumMetadata]:
        """Get metadata for a single album.

        Args:
            artist: Artist name
            album: Album name
            year: Optional album year for better search matching

        Returns:
            AlbumMetadata object or None if not found
        """
        from camoufox import AsyncCamoufox

        # Get browser options
        browser_options = self.browser_manager.get_browser_options()

        try:
            async with AsyncCamoufox(**browser_options) as browser:
                page = await browser.new_page()

                # Use the existing method that properly handles year parameter
                genre_data = await self.scraper.get_album_genres_and_descriptors(artist, album, year, page)

                if not genre_data:
                    return None

                genres = genre_data.get('genres', [])
                descriptors = genre_data.get('descriptors', [])

                # Build URL for reference (try direct first)
                url = self.scraper.build_direct_url(artist, album)

                return AlbumMetadata(
                    artist=artist,
                    album=album,
                    genres=genres,
                    descriptors=descriptors,
                    url=url
                )

        except Exception as e:
            self.logger.error(f"Error getting metadata for {artist} - {album}: {e}")
            return None

    async def get_multiple_albums_metadata(self, albums: List[Tuple[str, str, Optional[int]]]) -> List[Optional[AlbumMetadata]]:
        """Get metadata for multiple albums.

        Args:
            albums: List of (artist, album, year) tuples (year can be None)

        Returns:
            List of AlbumMetadata objects (None for failed lookups)
        """
        results = []
        for album_info in albums:
            if len(album_info) == 2:
                artist, album = album_info
                year = None
            else:
                artist, album, year = album_info
            result = await self.get_album_metadata(artist, album, year)
            results.append(result)
        return results

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


