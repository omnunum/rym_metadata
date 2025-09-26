"""Genre hierarchy management for RYM metadata enrichment."""

import json
import logging
import time
from pathlib import Path
from typing import Dict, List, Any


class GenreHierarchyManager:
    """Manages genre hierarchy data and parent genre expansion."""

    def __init__(self, cache_dir: str, cache_expiry_days: int = 30) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_expiry_days = cache_expiry_days
        self.logger = logging.getLogger(__name__)

        # Create cache directory if it doesn't exist
        self.cache_dir.mkdir(exist_ok=True)

        # Internal data structures
        self._hierarchy_data: Dict[str, Any] = {}  # genre_name -> genre info with depth & parents
        self._loaded = False

    @property
    def hierarchy_file_path(self) -> Path:
        """Get the path to the genre hierarchy JSON file."""
        return self.cache_dir / "genre_hierarchy.json"

    def is_cache_valid(self) -> bool:
        """Check if the cached genre hierarchy file exists and is not expired."""
        if not self.hierarchy_file_path.exists():
            return False

        if self.cache_expiry_days <= 0:
            return True  # No expiry

        # Check if file is within expiry window
        file_age = time.time() - self.hierarchy_file_path.stat().st_mtime
        max_age = self.cache_expiry_days * 24 * 60 * 60  # Convert days to seconds

        return file_age < max_age

    def load_hierarchy_data(self) -> bool:
        """Load genre hierarchy data from JSON file and build lookup structures."""
        if self._loaded and self._hierarchy_data:
            self.logger.debug("Genre hierarchy data already loaded")
            return True

        if not self.hierarchy_file_path.exists():
            self.logger.info(f"Genre hierarchy file not found: {self.hierarchy_file_path}")
            return False

        # Check if file is readable and not empty
        try:
            file_size = self.hierarchy_file_path.stat().st_size
            if file_size == 0:
                self.logger.warning(f"Genre hierarchy file is empty: {self.hierarchy_file_path}")
                return False

            self.logger.debug(f"Loading genre hierarchy file ({file_size} bytes): {self.hierarchy_file_path}")

        except Exception as e:
            self.logger.error(f"Error checking genre hierarchy file: {e}")
            return False

        try:
            with open(self.hierarchy_file_path, 'r', encoding='utf-8') as f:
                self._hierarchy_data = json.load(f)

            if not self._hierarchy_data:
                self.logger.warning("Loaded genre hierarchy data is empty")
                return False

            if not isinstance(self._hierarchy_data, dict):
                self.logger.error(f"Genre hierarchy data should be a dictionary, got {type(self._hierarchy_data)}")
                return False

            self._loaded = True

            self.logger.info(f"Successfully loaded {len(self._hierarchy_data)} genres from hierarchy file")
            return True

        except json.JSONDecodeError as e:
            self.logger.error(f"Invalid JSON in genre hierarchy file: {e}")
            return False
        except Exception as e:
            self.logger.error(f"Error loading genre hierarchy data: {e}")
            return False



    def expand_genres_with_parents(self, genre_names: List[str]) -> List[str]:
        """Expand a list of genre names to include all parent genres, sorted by specificity (most specific first)."""
        if not self._loaded:
            if not self.load_hierarchy_data():
                return genre_names

        unique_genres: Dict[str, int] = {}

        for genre_name in genre_names:
            genre_info = self._hierarchy_data.get(genre_name)
            if genre_info:
                # Add the genre with its depth
                unique_genres[genre_name] = genre_info['depth']

                # Add all parent genres
                for parent_name in genre_info['parents']:
                    parent_info = self._hierarchy_data.get(parent_name)
                    if parent_info:
                        unique_genres[parent_name] = parent_info['depth']
            else:
                self.logger.debug(f"Could not find genre: {genre_name}")
                # Add original genre with depth 0 as fallback
                unique_genres[genre_name] = 0

        # Sort by depth descending (most specific first) and return names
        sorted_genres = sorted(unique_genres.items(), key=lambda x: x[1], reverse=True)
        return [name for name, _ in sorted_genres]

    def get_stats(self) -> Dict[str, Any]:
        """Get statistics about the loaded genre hierarchy."""
        if not self._loaded:
            if not self.load_hierarchy_data():
                return {'loaded': False}

        return {
            'loaded': True,
            'total_genres': len(self._hierarchy_data),
            'cache_file': str(self.hierarchy_file_path),
            'cache_exists': self.hierarchy_file_path.exists(),
            'cache_valid': self.is_cache_valid()
        }