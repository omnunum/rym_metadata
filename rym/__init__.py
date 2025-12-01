"""RYM metadata scraping modules."""

__version__ = "1.4.3"

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

# Audio file tagging utilities
from .tagger import (
    find_audio_files,
    get_audio_metadata,
    write_rym_metadata,
    has_rym_metadata,
    group_files_by_album,
)

__all__ = [
    # Version
    '__version__',

    # Core API
    'RYMMetadataScraper',
    'RYMConfig',
    'RYMMetadata',

    # Audio file tagging
    'find_audio_files',
    'get_audio_metadata',
    'write_rym_metadata',
    'has_rym_metadata',
    'group_files_by_album',

    # Internal components (for advanced usage)
    'ProxySessionManager',
    'ContentCacheManager',
    'BrowserManager',
    'RYMScraper',
    'GenreHierarchyManager',
]