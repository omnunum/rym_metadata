"""HTML caching management for RYM scraping."""

import hashlib
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, Optional


class HtmlCacheManager:
    """Manages HTML caching for RYM pages."""

    def __init__(self, cache_dir: str, expiry_days: int = 0) -> None:
        self.cache_dir = Path(cache_dir)
        self.expiry_days = expiry_days
        self.logger = logging.getLogger(__name__)

        # Create cache directory if it doesn't exist
        self.cache_dir.mkdir(exist_ok=True)

    def _get_url_hash(self, url: str) -> str:
        """Generate SHA-256 hash for URL."""
        return hashlib.sha256(url.encode('utf-8')).hexdigest()

    def _get_cache_file(self, url: str) -> Path:
        """Get cache file path for URL."""
        url_hash = self._get_url_hash(url)
        return self.cache_dir / f"{url_hash}.json"

    def get_cached_html(self, url: str) -> Optional[str]:
        """Get cached HTML for URL if it exists and is not expired."""
        cache_file = self._get_cache_file(url)

        if not cache_file.exists():
            self.logger.debug(f"Cache miss: {url}")
            return None

        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)

            # Check if cache has expired (if expiry is set)
            if self.expiry_days > 0:
                cached_time = datetime.fromisoformat(cache_data['timestamp'])
                if datetime.now() - cached_time > timedelta(days=self.expiry_days):
                    self.logger.debug(f"Cache expired: {url}")
                    cache_file.unlink()  # Remove expired cache
                    return None

            self.logger.debug(f"Cache hit: {url}")
            return cache_data['html']

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            self.logger.warning(f"Corrupted cache file for {url}: {e}")
            cache_file.unlink()  # Remove corrupted cache
            return None

    def cache_html(self, url: str, html: str) -> None:
        """Cache HTML content for URL."""
        cache_file = self._get_cache_file(url)

        cache_data = {
            'url': url,
            'html': html,
            'timestamp': datetime.now().isoformat(),
            'expires': 'never' if self.expiry_days == 0 else (datetime.now() + timedelta(days=self.expiry_days)).isoformat()
        }

        try:
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(cache_data, f, ensure_ascii=False, indent=2)
            self.logger.debug(f"Cached HTML for: {url}")
        except IOError as e:
            self.logger.error(f"Failed to cache HTML for {url}: {e}")

    def clear_cache(self) -> int:
        """Clear all cached files."""
        try:
            cache_files = list(self.cache_dir.glob("*.json"))
            for cache_file in cache_files:
                cache_file.unlink()
            self.logger.info(f"Cleared {len(cache_files)} cache files")
            return len(cache_files)
        except Exception as e:
            self.logger.error(f"Error clearing cache: {e}")
            return 0

    def get_cache_info(self) -> Dict[str, Any]:
        """Get cache statistics."""
        try:
            cache_files = list(self.cache_dir.glob("*.json"))
            total_files = len(cache_files)

            total_size = sum(f.stat().st_size for f in cache_files)
            total_size_mb = total_size / (1024 * 1024)

            expired_count = 0
            if self.expiry_days > 0:
                for cache_file in cache_files:
                    try:
                        with open(cache_file, 'r', encoding='utf-8') as f:
                            cache_data = json.load(f)
                        cached_time = datetime.fromisoformat(cache_data['timestamp'])
                        if datetime.now() - cached_time > timedelta(days=self.expiry_days):
                            expired_count += 1
                    except (json.JSONDecodeError, KeyError, ValueError):
                        expired_count += 1  # Count corrupted files as expired

            return {
                'total_files': total_files,
                'total_size_mb': round(total_size_mb, 2),
                'expired_files': expired_count,
                'cache_dir': str(self.cache_dir),
                'expiry_days': self.expiry_days
            }
        except Exception as e:
            self.logger.error(f"Error getting cache info: {e}")
            return {}

    def cleanup_expired(self) -> int:
        """Clean up expired cache files. Returns number of files removed."""
        if self.expiry_days == 0:
            return 0  # No expiry set

        removed_count = 0
        try:
            cache_files = list(self.cache_dir.glob("*.json"))
            for cache_file in cache_files:
                try:
                    with open(cache_file, 'r', encoding='utf-8') as f:
                        cache_data = json.load(f)
                    cached_time = datetime.fromisoformat(cache_data['timestamp'])
                    if datetime.now() - cached_time > timedelta(days=self.expiry_days):
                        cache_file.unlink()
                        removed_count += 1
                        self.logger.debug(f"Removed expired cache: {cache_file.name}")
                except (json.JSONDecodeError, KeyError, ValueError):
                    # Remove corrupted cache files
                    cache_file.unlink()
                    removed_count += 1
                    self.logger.debug(f"Removed corrupted cache: {cache_file.name}")

            if removed_count > 0:
                self.logger.info(f"Cleaned up {removed_count} expired cache files")
            return removed_count
        except Exception as e:
            self.logger.error(f"Error during cache cleanup: {e}")
            return 0