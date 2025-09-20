"""Search and fuzzy matching functionality for RYM album discovery."""

import logging
import re
from difflib import SequenceMatcher
from typing import Dict, Any, Optional

from bs4 import BeautifulSoup


class RYMSearchEngine:
    """Handles RYM search operations and fuzzy matching for album discovery."""

    def __init__(self, scraper):
        self.scraper = scraper  # Reference to RYMScraper for fetch operations
        self.logger = logging.getLogger(__name__)

    async def search_album_url(self, search_url: str, page, artist: str, album: str, year: Optional[int] = None) -> Optional[str]:
        """Search for album URL on RYM search page using fuzzy matching."""
        html = await self.scraper.fetch_url_with_retry(search_url, page)
        if not html:
            return None

        try:
            soup = BeautifulSoup(html, 'html.parser')

            # Get all search results using the infobox structure
            infobox_row = soup.find('tr', class_='infobox')
            if not infobox_row:
                self.logger.debug("No infobox row found in search results")
                return None

            # Get all the nested tables within the infobox (each represents a search result)
            result_tables = infobox_row.find_all('table')
            if not result_tables:
                self.logger.debug("No result tables found in infobox")
                return None

            self.logger.info(f"Found {len(result_tables)} search result tables")

            # Extract candidate information from each result table
            candidates = []
            for i, table in enumerate(result_tables):
                candidate_info = self._extract_candidate_info(table)
                if candidate_info:
                    score = self._calculate_match_score(candidate_info, artist, album, year)
                    # Find the album link for the final return (same as in extract_candidate_info)
                    album_link = table.find('a', class_='searchpage')
                    if album_link:
                        candidates.append((score, candidate_info, album_link))
                        self.logger.info(f"Result {i}: {candidate_info['artist']} - {candidate_info['album']} ({candidate_info['year']}) Score: {score:.3f}")
                    else:
                        self.logger.debug(f"Result {i}: No album link found in table")

            if not candidates:
                self.logger.debug("No valid candidates found")
                return None

            # Sort by score (highest first) and return the best match
            candidates.sort(key=lambda x: x[0], reverse=True)
            best_score, best_info, best_link = candidates[0]

            self.logger.info(f"Best match: {best_info['artist']} - {best_info['album']} ({best_info['year']}) Score: {best_score:.3f}")

            relative_url = best_link['href']
            return f"http://rateyourmusic.com{relative_url}"

        except Exception as e:
            self.logger.error(f"Error parsing search results: {e}")

        return None

    def _extract_candidate_info(self, result_table) -> Optional[Dict[str, Any]]:
        """Extract artist, album, and year information from a search result table."""
        try:
            # Extract artist name from class="artist" link
            artist_link = result_table.find('a', class_='artist')
            if not artist_link:
                return None
            artist = artist_link.get_text(strip=True)

            # Extract album name from class="searchpage" link (the album link)
            album_link = result_table.find('a', class_='searchpage')
            if not album_link:
                return None
            album = album_link.get_text(strip=True)
            href = album_link.get('href', '')

            # Extract year from the table cells - look for a 4-digit year
            year = None
            table_cells = result_table.find_all('td')
            for cell in table_cells:
                cell_text = cell.get_text(strip=True)
                if re.match(r'^\d{4}$', cell_text):  # Exactly 4 digits
                    year = int(cell_text)
                    break

            return {
                'artist': artist,
                'album': album,
                'year': year,
                'url': href
            }

        except Exception as e:
            self.logger.debug(f"Error extracting candidate info: {e}")
            return None

    def _calculate_match_score(self, candidate: Dict[str, Any], target_artist: str, target_album: str, target_year: Optional[int] = None) -> float:
        """Calculate similarity score between candidate and target using fuzzy matching."""
        def string_similarity(s1: str, s2: str) -> float:
            """Calculate string similarity using SequenceMatcher."""
            if not s1 or not s2:
                return 0.0
            return SequenceMatcher(None, s1.lower().strip(), s2.lower().strip()).ratio()

        # Calculate individual similarity scores
        artist_score = string_similarity(candidate['artist'], target_artist)
        album_score = string_similarity(candidate['album'], target_album)

        # Year score (if available)
        year_score = 1.0  # Default if no year info
        if target_year and candidate['year']:
            year_diff = abs(candidate['year'] - target_year)
            if year_diff == 0:
                year_score = 1.0
            elif year_diff <= 1:
                year_score = 0.9
            elif year_diff <= 2:
                year_score = 0.7
            elif year_diff <= 5:
                year_score = 0.5
            else:
                year_score = 0.1

        # Weighted final score (artist and album are most important)
        final_score = (artist_score * 0.4) + (album_score * 0.4) + (year_score * 0.2)

        return final_score