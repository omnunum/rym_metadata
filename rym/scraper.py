"""Core scraping functionality for RYM metadata extraction."""

import asyncio
import logging
import re
from difflib import SequenceMatcher
from typing import Optional, Dict, Any, List
from urllib.parse import quote

from bs4 import BeautifulSoup

from .cache_manager import HtmlCacheManager
from .session_manager import ProxySessionManager
from .browser import BrowserManager

def _deduplicate_list(items: List[str]) -> List[str]:
    """Remove duplicates while preserving order."""
    seen = set()
    unique_items = []
    for item in items:
        if item not in seen:
            seen.add(item)
            unique_items.append(item)
    return unique_items


def _normalize_artist_name(name: str) -> str:
    """Normalize artist name for exact matching."""
    import unicodedata
    # Normalize unicode and remove accents
    normalized = unicodedata.normalize('NFD', name)
    ascii_name = ''.join(char for char in normalized if unicodedata.category(char) != 'Mn')
    # Lowercase and normalize whitespace
    return ' '.join(ascii_name.lower().split())


def _is_exact_artist_match(candidate: str, target: str) -> bool:
    """Check if artist names match exactly after normalization."""
    return _normalize_artist_name(candidate) == _normalize_artist_name(target)


class RYMScraper:
    """Handles core scraping operations for RYM album data."""

    def __init__(self, config: Any, cache_manager: Optional[HtmlCacheManager] = None,
                 session_manager: Optional[ProxySessionManager] = None,
                 browser_manager: Optional[BrowserManager] = None) -> None:
        self.config = config
        self.cache_manager = cache_manager
        self.session_manager = session_manager
        self.browser_manager = browser_manager
        self.logger = logging.getLogger(__name__)

    async def get_album_genres_and_descriptors(self, artist: str, album: str, year: Optional[int] = None, page: Any = None) -> Optional[Dict[str, Any]]:
        """Get genre and descriptor information for an album (beets-independent).

        Args:
            artist: Artist name
            album: Album name
            year: Optional album year for better matching
            page: Browser page object

        Returns:
            Dict with 'genres' and 'descriptors' lists, or None if not found
        """
        try:
            # Try direct URL first
            direct_url = self.build_direct_url(artist, album)
            self.logger.debug("Trying direct URL: %s", direct_url)

            # Test if direct URL works
            genre_data = await self.extract_genres_from_url(direct_url, page)
            genres = genre_data.get('genres', [])
            descriptors = genre_data.get('descriptors', [])

            # If direct URL fails, fall back to search
            if not genres:
                self.logger.debug(f"Direct URL failed, searching for {artist} - {album}")
                search_url = self.build_search_url(artist, album)

                # Use integrated search functionality
                album_url = await self._search_album_url(search_url, page, artist, album, year)

                if not album_url:
                    self.logger.debug(f"No RYM page found for {artist} - {album}")
                    return None

                # Fetch album page and extract genres
                genre_data = await self.extract_genres_from_url(album_url, page)
                genres = genre_data.get('genres', [])
                descriptors = genre_data.get('descriptors', [])

            return {'genres': genres, 'descriptors': descriptors}

        except Exception as e:
            self.logger.error(f"Error processing {artist} - {album}: {e}")
            return None

    async def get_artist_genres_and_descriptors(self, artist: str, page: Any = None) -> Optional[Dict[str, Any]]:
        """Get genre and descriptor information for an artist (beets-independent).

        Args:
            artist: Artist name
            page: Browser page object

        Returns:
            Dict with 'genres' and 'descriptors' lists, or None if not found
        """
        try:
            # Try direct artist URL first
            direct_url = self.build_artist_url(artist)
            self.logger.debug("Trying direct artist URL: %s", direct_url)

            # Test if direct URL works
            genre_data = await self.extract_artist_genres_from_url(direct_url, page)
            genres = genre_data.get('genres', [])
            descriptors = genre_data.get('descriptors', [])

            # If direct URL fails, fall back to search
            if not genres:
                self.logger.debug(f"Direct URL failed, searching for artist {artist}")
                search_url = self.build_artist_search_url(artist)

                # Use search functionality for artists
                artist_url = await self._search_artist_url(search_url, page, artist)

                if not artist_url:
                    self.logger.debug(f"No RYM page found for artist {artist}")
                    return None

                # Fetch artist page and extract genres
                genre_data = await self.extract_artist_genres_from_url(artist_url, page)
                genres = genre_data.get('genres', [])
                descriptors = genre_data.get('descriptors', [])

            return {'genres': genres, 'descriptors': descriptors}

        except Exception as e:
            self.logger.error(f"Error processing artist {artist}: {e}")
            return None

    async def process_single_album(self, album_obj: Any, page: Any, dry_run: bool = False) -> Optional[tuple[Any, Dict[str, Any]]]:
        """Process a single album and extract genre information (beets-compatible wrapper).

        This method maintains compatibility with beets Album objects.
        """
        try:
            # Extract data from album object (works with beets Album)
            artist = getattr(album_obj, 'albumartist', '') or getattr(album_obj, 'artist', '')
            album_name = getattr(album_obj, 'album', '')
            year = getattr(album_obj, 'year', None)

            # Get genre data using the generic method
            genre_data = await self.get_album_genres_and_descriptors(artist, album_name, year, page)

            if not genre_data:
                return None

            genres = genre_data.get('genres', [])
            descriptors = genre_data.get('descriptors', [])

            if (genres or descriptors) and not dry_run:
                # Store genres and descriptors in the album (beets-specific)
                if genres:
                    album_obj['genres'] = '; '.join(genres)
                if descriptors:
                    album_obj['descriptors'] = '; '.join(descriptors)
                if hasattr(album_obj, 'store'):
                    album_obj.store()

            return album_obj, genre_data

        except Exception as e:
            artist = getattr(album_obj, 'albumartist', 'Unknown')
            album_name = getattr(album_obj, 'album', 'Unknown')
            self.logger.error(f"Error processing {artist} - {album_name}: {e}")
            return None

    def build_direct_url(self, artist: str, album_name: str) -> str:
        """Build direct RYM URL for the given artist and album."""
        import unicodedata

        def clean_for_url(text: str) -> str:
            # Normalize unicode and remove accents
            text = unicodedata.normalize('NFD', text)
            text = ''.join(char for char in text if unicodedata.category(char) != 'Mn')
            # Remove non-word characters and convert to lowercase
            text = re.sub(r'[^\w\s]', '', text.lower()).strip()
            # Replace spaces with hyphens
            return re.sub(r'\s+', '-', text)

        artist_clean = clean_for_url(artist)
        album_clean = clean_for_url(album_name)

        # Use HTTP since HTTPS has proxy issues
        return f"http://rateyourmusic.com/release/album/{artist_clean}/{album_clean}/"

    def build_search_url(self, artist: str, album_name: str) -> str:
        """Build RYM search URL for the given artist and album."""
        # Clean up artist and album names - replace non-word chars with spaces
        artist_clean = re.sub(r'[^\w\s]', ' ', artist).strip()
        album_clean = re.sub(r'[^\w\s]', ' ', album_name).strip()

        # Normalize multiple spaces to single spaces
        artist_clean = re.sub(r'\s+', ' ', artist_clean)
        album_clean = re.sub(r'\s+', ' ', album_clean)

        # Build search query
        query = f"{artist_clean} {album_clean}".strip()
        encoded_query = quote(query)

        return f"http://rateyourmusic.com/search?searchtype=l&searchterm={encoded_query}"

    def build_artist_url(self, artist: str) -> str:
        """Build direct RYM artist URL for the given artist."""
        import unicodedata

        def clean_for_url(text: str) -> str:
            # Normalize unicode and remove accents
            text = unicodedata.normalize('NFD', text)
            text = ''.join(char for char in text if unicodedata.category(char) != 'Mn')
            # Remove non-word characters and convert to lowercase
            text = re.sub(r'[^\w\s]', '', text.lower()).strip()
            # Replace spaces with hyphens
            return re.sub(r'\s+', '-', text)

        artist_clean = clean_for_url(artist)
        return f"http://rateyourmusic.com/artist/{artist_clean}"

    def build_artist_search_url(self, artist: str) -> str:
        """Build RYM search URL for the given artist."""
        # Clean up artist name - replace non-word chars with spaces
        artist_clean = re.sub(r'[^\w\s]', ' ', artist).strip()
        # Normalize multiple spaces to single spaces
        artist_clean = re.sub(r'\s+', ' ', artist_clean)
        encoded_query = quote(artist_clean)
        return f"http://rateyourmusic.com/search?searchtype=a&searchterm={encoded_query}"

    async def extract_genres_from_url(self, url: str, page: Any) -> Dict[str, list[str]]:
        """Extract genre information and descriptors from an RYM album page using async."""
        html = await self.fetch_url_with_retry(url, page)
        if not html:
            return {'genres': [], 'descriptors': []}

        try:
            soup = BeautifulSoup(html, 'html.parser')

            # Fail fast: Check if this looks like a valid album page
            genre_row = soup.find('tr', class_='release_genres')
            if not genre_row:
                # No release_genres row means this isn't a valid album page
                return {'genres': [], 'descriptors': []}

            genres = []
            descriptors = []

            # Extract genres from the release_pri_genres span
            pri_genres = genre_row.find('span', class_='release_pri_genres')
            if pri_genres:
                genre_links = pri_genres.find_all('a', class_='genre')
                for link in genre_links:
                    genre_text = link.get_text(strip=True)
                    if genre_text:
                        genres.append(genre_text)

            # Look for descriptors in the release_descriptors row
            descriptor_row = soup.find('tr', class_='release_descriptors')
            if descriptor_row:
                # Find all meta tags with content attribute
                meta_tags = descriptor_row.find_all('meta', content=True)
                for meta in meta_tags:
                    descriptor = meta.get('content', '').strip()
                    if descriptor:
                        descriptors.append(descriptor)


            return {
                'genres': _deduplicate_list(genres),
                'descriptors': _deduplicate_list(descriptors)
            }

        except Exception as e:
            self.logger.error(f"Error extracting genres from {url}: {e}")
            return {'genres': [], 'descriptors': []}

    async def extract_artist_genres_from_url(self, url: str, page: Any) -> Dict[str, list[str]]:
        """Extract genre information and descriptors from an RYM artist page using async."""
        html = await self.fetch_url_with_retry(url, page)
        if not html:
            return {'genres': [], 'descriptors': []}

        try:
            soup = BeautifulSoup(html, 'html.parser')
            genres = []
            descriptors = []

            # Look for genres in artist-specific sections only
            # Find the "Genres" header and extract from the following info_content div
            info_headers = soup.find_all('div', class_='info_hdr')
            for header in info_headers:
                if header.get_text(strip=True).lower() != 'genres':
                    continue

                # Find the next info_content div after the Genres header
                info_content = header.find_next_sibling('div', class_='info_content')
                if not info_content:
                    break

                genre_links = info_content.find_all('a', class_='genre')
                for link in genre_links:
                    genre_text = link.get_text(strip=True)
                    if not genre_text:
                        continue
                    genres.append(genre_text)
                break


            return {
                'genres': _deduplicate_list(genres),
                'descriptors': _deduplicate_list(descriptors)
            }

        except Exception as e:
            self.logger.error(f"Error extracting artist genres from {url}: {e}")
            return {'genres': [], 'descriptors': []}

    async def fetch_url_with_retry(self, url: str, page: Any) -> Optional[str]:
        """Fetch URL using AsyncCamoufox with automatic captcha solving and session management."""
        # Check cache first
        if self.cache_manager:
            cached_html = self.cache_manager.get_cached_html(url)
            if cached_html:
                return cached_html

        max_retries = self.config.max_retries

        for attempt in range(max_retries + 1):
            try:
                self.logger.debug(f"Fetching URL (attempt {attempt + 1}): {url}")

                # Check if we have a valid session and apply cookies
                if self.session_manager and self.session_manager.is_session_valid():
                    await self.browser_manager.apply_session_cookies(page)
                    self.logger.debug("Using existing session cookies")
                    # Set up resource blocking since we already have a valid session
                    await self.browser_manager.setup_resource_blocking(page)

                # Navigate to URL
                await page.goto(url, wait_until='domcontentloaded')

                # Wait for network to be idle
                try:
                    await page.wait_for_load_state('networkidle', timeout=10000)
                except Exception:
                    # Fallback if networkidle fails
                    await asyncio.sleep(2)

                # Attempt to solve any Cloudflare challenge automatically
                try:
                    challenge_solved = await self.browser_manager.solve_cloudflare_challenge(page, url)
                    if challenge_solved:
                        # Wait a bit more after challenge is solved
                        await page.wait_for_load_state('networkidle', timeout=5000)
                except Exception as e:
                    # If challenge solving fails, we might still have gotten through
                    self.logger.debug(f"Challenge solving attempt failed: {e}")

                # Get page source
                html = await page.content()

                # Basic validation
                if html and len(html) > 1000:  # Ensure we got substantial content
                    # Cache successful response
                    if self.cache_manager:
                        self.cache_manager.cache_html(url, html)

                    # Increment request count for successful request
                    if self.session_manager:
                        self.session_manager.increment_request_count()
                    return html
                else:
                    self.logger.debug(f"Got minimal content, may be blocked: {len(html) if html else 0} chars")

            except Exception as e:
                error_msg = str(e)
                self.logger.debug(f"Attempt {attempt + 1} failed for {url}: {error_msg}")

                # Check for specific proxy errors that indicate port should be rotated
                if any(error in error_msg for error in ["PROXY_FORBIDDEN", "403", "PROXY_CONNECTION_FAILED", "CONNECTION_REFUSED"]):
                    if self.session_manager and self.config.auto_rotate_on_failure:
                        self.logger.warning("Proxy error detected, marking port as blocked")
                        self.session_manager.mark_port_blocked()
                        if self.session_manager.rotate_port():
                            self.logger.info("Rotated to new port, will retry")
                            # Port rotation handled by session manager
                            continue
                        else:
                            self.logger.error("No more ports available")
                            return None
                    elif self.session_manager:
                        self.logger.warning("Proxy error detected but auto_rotate_on_failure is disabled")
                        return None

                # Check for other errors
                if "CERTIFICATE" in error_msg.upper():
                    self.logger.warning("SSL certificate issue - may need custom certificate configuration")

            if attempt < max_retries:
                retry_delay = self.config.retry_delay
                await asyncio.sleep(retry_delay * (attempt + 1))  # Exponential backoff

        self.logger.warning(f"Failed to fetch {url} after {max_retries + 1} attempts")
        return None

    async def _search_album_url(self, search_url: str, page: Any, artist: str, album: str, year: Optional[int] = None) -> Optional[str]:
        """Search for album URL on RYM search page using fuzzy matching."""
        html = await self.fetch_url_with_retry(search_url, page)
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

            # Sort by score (highest first) and check threshold
            candidates.sort(key=lambda x: x[0], reverse=True)
            best_score, best_info, best_link = candidates[0]

            self.logger.info(f"Best match: {best_info['artist']} - {best_info['album']} ({best_info['year']}) Score: {best_score:.3f}")

            # Check if best match meets the threshold
            matching_threshold = self.config.matching_threshold
            if best_score < matching_threshold:
                self.logger.info(f"Best match score {best_score:.3f} below threshold {matching_threshold:.3f}, rejecting match")
                return None

            relative_url = best_link['href']
            return f"http://rateyourmusic.com{relative_url}"

        except Exception as e:
            self.logger.error(f"Error parsing search results: {e}")

        return None

    async def _search_artist_url(self, search_url: str, page: Any, artist: str) -> Optional[str]:
        """Search for artist URL on RYM search page using fuzzy matching."""
        html = await self.fetch_url_with_retry(search_url, page)
        if not html:
            return None

        try:
            soup = BeautifulSoup(html, 'html.parser')

            # For artist search, look for results with class="searchpage" that link to artists
            artist_links = soup.find_all('a', class_='searchpage', href=re.compile(r'/artist/'))

            if not artist_links:
                self.logger.debug("No artist links found in search results")
                return None

            # Look for exact match only (after normalization)
            for link in artist_links:
                link_text = link.get_text(strip=True)
                if _is_exact_artist_match(link_text, artist):
                    self.logger.info(f"Found exact artist match: '{link_text}' matches '{artist}'")
                    relative_url = link['href']
                    return f"http://rateyourmusic.com{relative_url}"

            # No exact match found
            self.logger.info(f"No exact match found for artist '{artist}' in search results")

        except Exception as e:
            self.logger.error(f"Error parsing artist search results: {e}")

        return None

    def _calculate_string_similarity(self, s1: str, s2: str) -> float:
        """Calculate string similarity using SequenceMatcher."""
        if not s1 or not s2:
            return 0.0
        from difflib import SequenceMatcher
        return SequenceMatcher(None, s1.lower().strip(), s2.lower().strip()).ratio()

    def _extract_candidate_info(self, result_table: Any) -> Optional[Dict[str, Any]]:
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