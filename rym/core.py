"""Core RYM metadata scraping functionality independent of beets.

This module provides a clean interface for scraping RateYourMusic metadata
that can be used standalone or integrated into other tools like streamrip.
"""

import logging
import os
from typing import Dict, List, Optional, Any, Literal
from dataclasses import dataclass

from .session_manager import ProxySessionManager
from .content_cache_manager import ContentCacheManager
from .browser import BrowserManager
from .scraper import RYMScraper

from camoufox import AsyncCamoufox

@dataclass
class AlbumMetadata:
    """Container for album metadata extracted from RYM."""
    artist: str
    album: str
    genres: List[str]
    descriptors: List[str]
    url: Optional[str] = None
    album_type: Optional[str] = "album"


@dataclass
class ArtistMetadata:
    """Container for artist metadata extracted from RYM."""
    artist: str
    genres: List[str]
    descriptors: List[str]
    url: Optional[str] = None


@dataclass
class RYMConfig:
    """Configuration for standalone RYM scraper."""
    # Proxy configuration
    proxy_enabled: bool = False  # Disabled by default for simplicity
    proxy_host: Optional[str] = None
    proxy_port: Optional[int] = None
    proxy_username: Optional[str] = None
    proxy_password: Optional[str] = None
    proxy_use_tls: bool = False
    proxy_cert_path: Optional[str] = None

    # Proxy rotation method
    proxy_rotation_method: Literal['port', 'username'] = 'port'  # How IPs are rotated
    auto_rotate_on_failure: bool = True  # Auto-rotate when proxy errors occur

    # Session management (controls timing/request patterns)
    session_type: Literal['sticky', 'rotate', 'const'] = 'const'  # When/how sessions change
    session_duration: int = 600
    session_id_length: int = 10
    port_range_start: int = 10001
    port_range_end: int = 10100

    # Browser and retry settings
    max_retries: int = 3
    retry_delay: float = 2.0
    page_timeout: int = 30000

    # Rate limiting
    min_request_interval: float = 3.0  # Minimum seconds between requests (0 = disabled)
    humanize_request_interval: bool = True  # Add ±25% random jitter to intervals

    # Cache settings
    cache_enabled: bool = True
    cache_dir: str = '.rym_cache'
    cache_expiry_days: int = 7  # Cache for a week by default

    # Session state file path
    session_state_file_path: Optional[str] = None  # Defaults to .rym_session_state.json in current directory

    # Resource blocking
    resource_blocking_enabled: bool = True

    # Search matching
    matching_threshold: float = 0.8  # Minimum similarity score (0.0-1.0) for accepting matches

    # Genre expansion
    expand_parent_genres: bool = True  # Automatically add parent genres to album metadata
    genre_cache_expiry_days: int = 30  # How long to cache genre hierarchy data (0 = never expire)

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

            # Proxy rotation method
            proxy_rotation_method=config['proxy_rotation_method'].get('port'),
            auto_rotate_on_failure=config['auto_rotate_on_failure'].get(True),

            # Session management
            session_type=config['session_type'].get('const'),
            session_duration=config['session_duration'].get(600),
            session_id_length=config['session_id_length'].get(10),
            port_range_start=config['port_range_start'].get(10001),
            port_range_end=config['port_range_end'].get(10100),

            # Browser and retry settings
            max_retries=config['max_retries'].get(3),
            retry_delay=config['retry_delay'].get(2.0),
            page_timeout=config['page_timeout'].get(30000),

            # Rate limiting
            min_request_interval=config['min_request_interval'].get(3.0),
            humanize_request_interval=config['humanize_request_interval'].get(True),

            # Cache settings
            cache_enabled=config['cache_enabled'].get(True),
            cache_dir=config['cache_dir'].get('.rym_cache'),
            cache_expiry_days=config['cache_expiry_days'].get(0),

            # Session state file path
            session_state_file_path=config['session_state_file_path'].get(),

            # Resource blocking
            resource_blocking_enabled=config['resource_blocking_enabled'].get(True),

            # Search matching
            matching_threshold=config['matching_threshold'].get(0.8),

            # Genre expansion
            expand_parent_genres=config['expand_parent_genres'].get(True),
            genre_cache_expiry_days=config['genre_cache_expiry_days'].get(30),
        )

    @property
    def proxy_server_url(self) -> Optional[str]:
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
            cache_dir = self.config.cache_dir
            if not os.path.isabs(cache_dir):
                cache_dir = os.path.abspath(cache_dir)

            self.cache_manager = ContentCacheManager(cache_dir)

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

    async def __aenter__(self):
        """Async context manager entry - start browser session."""
        await self.scraper.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit - cleanup browser session."""
        await self.scraper.__aexit__(exc_type, exc_val, exc_tb)


    async def get_album_metadata(self, artist: str, album: str, year: Optional[int] = None, album_type: Literal["album", "single", "ep", "compilation"] = "album") -> Optional[AlbumMetadata]:
        """Get metadata for a single album.

        Args:
            artist: Artist name
            album: Album name
            year: Optional album year for better search matching
            album_type: Type of release ("album", "single", "ep", "compilation")

        Returns:
            AlbumMetadata object or None if not found
        """
        try:
            # Get album metadata, with artist fallback
            result = await self.scraper.get_album_genres_and_descriptors(artist, album, year, album_type)
            if not result:
                # Fall back to artist genres if album search fails
                result = await self.scraper.get_artist_genres_and_descriptors(artist)

            if not result:
                return None

            genres, descriptors = result

            # Build URL for reference (try direct first)
            url = self.scraper.build_direct_url(artist, album, album_type)

            return AlbumMetadata(
                artist=artist,
                album=album,
                genres=genres,
                descriptors=descriptors,
                url=url,
                album_type=album_type
            )

        except Exception as e:
            self.logger.error(f"Error getting metadata for {artist} - {album}: {e}")
            return None

    async def get_artist_metadata(self, artist: str) -> Optional[ArtistMetadata]:
        """Get metadata for a single artist.

        Args:
            artist: Artist name

        Returns:
            ArtistMetadata object or None if not found
        """
        try:
            # Get artist metadata directly
            result = await self.scraper.get_artist_genres_and_descriptors(artist)

            if not result:
                return None

            genres, descriptors = result

            # Build URL for reference (try direct first)
            url = self.scraper.build_artist_url(artist)

            return ArtistMetadata(
                artist=artist,
                genres=genres,
                descriptors=descriptors,
                url=url
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





