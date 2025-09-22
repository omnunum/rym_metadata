"""RYM metadata scraping modules."""

# Core standalone functionality (for streamrip and other integrations)
from .core import (
    RYMMetadataScraper,
    RYMConfig,
    AlbumMetadata,
    ArtistMetadata,
)

# Internal components (for advanced usage)
from .session_manager import ProxySessionManager
from .cache_manager import HtmlCacheManager
from .browser import BrowserManager
from .scraper import RYMScraper

__all__ = [
    # Core API
    'RYMMetadataScraper',
    'RYMConfig',
    'AlbumMetadata',
    'ArtistMetadata',

    # Internal components (for advanced usage)
    'ProxySessionManager',
    'HtmlCacheManager',
    'BrowserManager',
    'RYMScraper',
]