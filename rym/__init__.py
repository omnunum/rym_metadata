"""RYM metadata scraping modules."""

# Core standalone functionality (for streamrip and other integrations)
from .dataclasses import RYMConfig, RYMMetadata
from .core import (
    RYMMetadataScraper,
)

# Internal components (for advanced usage)
from .session_manager import ProxySessionManager
from .content_cache_manager import ContentCacheManager
from .browser import BrowserManager
from .scraper import RYMScraper
from .genre_manager import GenreHierarchyManager

__all__ = [
    # Core API
    'RYMMetadataScraper',
    'dataclasses',
    'RYMMetadata',

    # Internal components (for advanced usage)
    'ProxySessionManager',
    'ContentCacheManager',
    'BrowserManager',
    'RYMScraper',
    'GenreHierarchyManager',
]