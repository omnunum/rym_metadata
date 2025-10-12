"""Core scraping functionality for RYM metadata extraction."""

import asyncio
import json
import logging
import random
import re
import time
from difflib import SequenceMatcher
from typing import Optional, Dict, Any, List, Literal
from urllib.parse import quote

from bs4 import BeautifulSoup
from camoufox import AsyncCamoufox
from tenacity import retry, stop_after_attempt, wait_exponential

from rym.dataclasses import DiscographyCandidate

from .content_cache_manager import ContentCacheManager
from .browser import BrowserManager
from .text_utils import normalize_text
from .genre_manager import GenreHierarchyManager

BASE_URL = "https://rateyourmusic.com"


def _deduplicate_list(items: List[str]) -> List[str]:
    """Remove duplicates while preserving order."""
    seen = set()
    unique_items = []
    for item in items:
        if item not in seen:
            seen.add(item)
            unique_items.append(item)
    return unique_items




class RYMScraper:
    """Handles core scraping operations for RYM album data.

    Album Matching Strategy - Two-Phase Normalization:

    When searching for albums on RYM, a two-phase normalization approach is used
    to handle RYM's substring-based search while maintaining accurate fuzzy matching:

    Phase 1 - Search Query (Aggressive Normalization):
        Used when querying RYM's search/filter APIs to cast a wide net.
        Strips album names to core content words by removing:
        - Articles (the, a, an)
        - Volume/part/number words (volume, part, featuring, etc.)
        - All numerals (Arabic and Roman)
        - Common prepositions (of, for, with, and, etc.)

        Example: "The Alchemy Index, Vol. 3 & 4: Air & Earth" → "alchemy index air earth"
        Function: _normalize_album_for_search()

    Phase 2 - Match Scoring (Moderate Normalization):
        Used when scoring candidate matches from search results for accuracy.
        Applies semantic normalization:
        - Expands abbreviations (vol → volume, ft → featuring)
        - Converts Arabic numerals to Roman (3 → iii, 4 → iv)
        - Removes punctuation
        - Keeps articles and prepositions for accurate similarity scoring

        Example: "vol 3 & 4" → "volume iii and iv"
        Function: _normalize_album_name()

    This two-phase approach significantly improves match rates for albums with
    volume numbers, Roman numerals, and verbose titles.
    """

    def __init__(self, config: Any, cache_manager: Optional[ContentCacheManager] = None,
                 browser_manager: Optional[BrowserManager] = None) -> None:
        self.config = config
        self.cache_manager = cache_manager
        self.browser_manager = browser_manager
        self.logger = logging.getLogger(__name__)

        # Rate limiting
        self._last_request_time: Optional[float] = None

        # Global request serialization lock to prevent concurrent challenge solving
        # This ensures only one request is active at a time, preventing race conditions
        # when multiple requests hit Cloudflare challenges simultaneously
        self._request_lock = asyncio.Lock()

        # Browser state management - only browser context, pages created as needed
        self._browser = None
        self._browser_context = None

        # Genre hierarchy management - initialize if cache is available
        # This allows us to load existing genre data and scrape new data when needed
        self.genre_manager = None
        if self.cache_manager:
            self.genre_manager = GenreHierarchyManager(
                str(self.cache_manager.cache_dir),
                self.config.genre_cache_expiry_days
            )

    async def __aenter__(self):
        """Async context manager entry - start browser session."""
        await self._start_browser_session()

        # Ensure genre hierarchy is available after browser session is established
        await self._ensure_genre_hierarchy_available()

        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit - cleanup browser session."""
        # Standard context manager parameters (unused but required by protocol)
        del exc_type, exc_val, exc_tb  # Explicitly mark as intentionally unused
        await self._cleanup_browser_session()

    async def _start_browser_session(self) -> None:
        """Start a persistent browser session for multiple requests."""
        if self._browser is not None:
            return  # Already started

        # Get browser options
        browser_options = self.browser_manager.get_browser_options()

        try:
            self._browser = await AsyncCamoufox(**browser_options).__aenter__()

            # Create a single browser context that we'll use for all operations
            self._browser_context = await self._browser.new_context()
            self.logger.debug("Browser session and context started successfully")

            # Load saved session cookies if available
            # NOTE: This attempts to reuse cookies from previous sessions. This can help reduce
            # challenge frequency if starting with the same IP (e.g., const session type).
            # However, with port-based rotation, the IP may have changed since last run, making
            # these cookies invalid. Cloudflare may reject them or present a challenge.
            # If this causes issues (false positives, bans), it's safe to remove this line -
            # the first request will simply hit a challenge and get fresh cookies for current IP.
            await self.browser_manager.apply_session_cookies_to_context(self._browser_context)

        except (ConnectionError, TimeoutError) as e:
            self.logger.error(f"Network error starting browser session: {e}")
            self._browser = None
            self._browser_context = None
            raise
        except ImportError as e:
            self.logger.error(f"Missing browser dependencies: {e}")
            self._browser = None
            self._browser_context = None
            raise
        except Exception as e:
            self.logger.error(f"Unexpected error starting browser session: {e}")
            self._browser = None
            self._browser_context = None
            raise


    async def _ensure_genre_hierarchy_available(self) -> None:
        """Ensure genre hierarchy data is available, loading from cache or scraping if needed."""
        if not self.genre_manager:
            self.logger.debug("No genre manager available, skipping genre hierarchy check")
            return

        # Check if we already have valid cached genre data
        if self.genre_manager.is_cache_valid():
            if self.genre_manager.load_hierarchy_data():
                self.logger.info("Genre hierarchy data loaded from existing cache")
                return
            else:
                self.logger.warning("Failed to load genre hierarchy data from cache, will attempt to scrape")

        # Cache is invalid or doesn't exist, scrape new data
        self.logger.info("Genre hierarchy cache is invalid or missing, scraping fresh data...")
        try:
            genre_file_path = await self._scrape_genre_hierarchy()
            if genre_file_path:
                # Try to load the freshly scraped data
                if self.genre_manager.load_hierarchy_data():
                    self.logger.info(f"Successfully scraped and loaded genre hierarchy data from {genre_file_path}")
                else:
                    self.logger.error("Failed to load freshly scraped genre hierarchy data")
            else:
                self.logger.warning("Failed to scrape genre hierarchy data")
        except Exception as e:
            self.logger.error(f"Error ensuring genre hierarchy availability: {e}")

    async def _cleanup_browser_session(self) -> None:
        """Clean up the persistent browser session."""
        if self._browser is not None:
            try:
                await self._browser.__aexit__(None, None, None)
                self.logger.debug("Browser session cleaned up")
            except Exception as e:
                self.logger.warning(f"Error during browser cleanup: {e}")
            finally:
                self._browser = None


    async def _create_page(self) -> Any:
        """Create a new page with resource blocking set up.

        Note: Session cookies are automatically inherited from browser context.
        """
        if self._browser is None:
            raise RuntimeError("Browser session not started. Use async context manager.")

        # Use the same browser context that has our cookies
        browser_context = self._browser.contexts[0] if self._browser.contexts else await self._browser.new_context()
        page = await browser_context.new_page()

        # Set up resource blocking on page creation
        await self.browser_manager.setup_resource_blocking(page)

        # Note: Session cookies are automatically inherited from browser context
        # No need to apply cookies per-page since they're applied at context level
        self.logger.debug("Created new page (cookies inherited from browser context)")

        return page


    async def navigate_page_with_rate_limiting(self, url: str, page: Any, response_type: str = 'html') -> Optional[Any]:
        """Navigate with scraper-specific rate limiting.

        Args:
            url: URL to navigate to
            page: Playwright page object
            response_type: 'html' for HTML content (default), 'json' for JSON API responses

        Returns:
            HTML string for response_type='html', parsed JSON for response_type='json'
        """
        # Rate limiting (scraper timing behavior)
        await self._wait_for_rate_limit()

        # Delegate to appropriate fetch method based on response type
        if response_type == 'json':
            result = await self.browser_manager.fetch_ajax_json(page, url)
        else:  # 'html'
            result = await self.browser_manager.fetch_html(page, url)

        # Update scraper timing for rate limiting
        self._update_request_time()

        return result


    async def get_album_genres_and_descriptors(self, artist: str, album: str, year: Optional[int] = None, album_type: Literal["album", "single", "ep", "compilation"] = "album") -> Optional[tuple[list[str], list[str]]]:
        """Get genre and descriptor information for a specific album.

        Args:
            artist: Artist name
            album: Album name
            year: Optional album year for better matching
            album_type: Type of album release

        Returns:
            Tuple of (genres, descriptors) lists, or None if not found
        """
        # Serialize all requests to prevent concurrent challenge solving attempts
        async with self._request_lock:
            page = await self._create_page()
            try:
                self.logger.info(f"Searching for album: {artist} - {album}")

                # Check release content cache first
                if self.cache_manager:
                    cached_html = self.cache_manager.get_cached_content("release", artist, album)
                    if cached_html:
                        genres, descriptors = self._extract_genres_from_html(cached_html)
                        if genres or descriptors:
                            return genres, descriptors

                # Try direct album URL
                direct_url = self.build_direct_url(artist, album, album_type)
                self.logger.debug(f"Trying direct album URL: {direct_url}")

                html = await self.navigate_page_with_rate_limiting(direct_url, page)
                genres, descriptors = self._extract_genres_from_html(html) if html else ([], [])

                # Cache successful album result
                if html and (genres or descriptors) and self.cache_manager:
                    self.cache_manager.save_content("release", artist, html, album)

                # If direct URL fails, try artist ID cache + discography search
                if not genres:
                    self.logger.info(f"Direct album URL failed, checking artist ID cache for {artist}")

                    cached_artist_id = None
                    if self.cache_manager:
                        cached_artist_id = self.cache_manager.lookup_artist_id(artist)

                    if cached_artist_id:
                        # Use cached artist ID for direct discography search
                        self.logger.info(f"Using cached artist ID for discography search")
                        album_url = await self._search_discography_by_artist_id(cached_artist_id, album, page, year)

                        if album_url:
                            html = await self.navigate_page_with_rate_limiting(album_url, page)
                            genres, descriptors = self._extract_genres_from_html(html) if html else ([], [])

                            # Cache successful result
                            if html and (genres or descriptors) and self.cache_manager:
                                self.cache_manager.save_content("release", artist, html, album)
                        else:
                            # Artist exists (we have cached ID) but album not found in discography
                            # No point in doing redundant artist search - fail fast
                            self.logger.info(f"Artist {artist} found in cache but album {album} not in discography")

                    # Only try full artist page approach if we don't have cached artist ID
                    else:
                        self.logger.info(f"No cached artist ID, trying full artist page approach for {artist} - {album}")

                        artist_page_url = await self._get_artist_page_url(artist, page)

                        if artist_page_url:
                            album_url = await self._search_artist_discography(artist_page_url, album, page, year)

                            if album_url:
                                html = await self.navigate_page_with_rate_limiting(album_url, page)
                                genres, descriptors = self._extract_genres_from_html(html) if html else ([], [])

                                # Cache successful result
                                if html and (genres or descriptors) and self.cache_manager:
                                    self.cache_manager.save_content("release", artist, html, album)

                # Return results or None
                if genres or descriptors:
                    return genres, descriptors
                else:
                    self.logger.info(f"No genres found for {artist} - {album}")
                    return None, None

            except Exception as e:
                self.logger.error(f"Error processing {artist} - {album}: {e}")
                return None, None
            finally:
                # Close page to prevent memory leaks during long sessions
                await page.close()

    async def get_artist_genres_and_descriptors(self, artist: str) -> Optional[tuple[list[str], list[str]]]:
        """Get genre and descriptor information for a specific artist.

        Args:
            artist: Artist name

        Returns:
            Tuple of (genres, descriptors) lists, or None if not found
        """
        # Serialize all requests to prevent concurrent challenge solving attempts
        async with self._request_lock:
            page = await self._create_page()
            try:
                self.logger.info(f"Searching for artist: {artist}")

                # Check artist content cache first
                if self.cache_manager:
                    cached_html = self.cache_manager.get_cached_content("artist", artist)
                    if cached_html:
                        genres, descriptors = self._extract_genres_from_html(cached_html, "artist")
                        if genres or descriptors:
                            return genres, descriptors

                # Try direct artist URL
                direct_url = self.build_artist_url(artist)
                self.logger.debug(f"Trying direct artist URL: {direct_url}")

                html = await self.navigate_page_with_rate_limiting(direct_url, page)
                genres, descriptors = self._extract_genres_from_html(html, "artist") if html else ([], [])

                # Cache successful artist result
                if html and (genres or descriptors) and self.cache_manager:
                    self.cache_manager.save_content("artist", artist, html)

                # If direct artist URL fails, try artist search
                if not genres:
                    self.logger.debug(f"Direct artist URL failed, searching for artist {artist}")
                    search_url = self.build_artist_search_url(artist)

                    artist_url = await self._search_artist_url(search_url, page, artist)

                    if artist_url:
                        html = await self.navigate_page_with_rate_limiting(artist_url, page)
                        genres, descriptors = self._extract_genres_from_html(html, "artist") if html else ([], [])

                        # Cache successful result
                        if html and (genres or descriptors) and self.cache_manager:
                            self.cache_manager.save_content("artist", artist, html)

                # Return results or None
                if genres or descriptors:
                    return genres, descriptors
                else:
                    self.logger.info(f"No genres found for artist {artist}")
                    return None, None

            except Exception as e:
                self.logger.error(f"Error processing artist {artist}: {e}")
                return None, None
            finally:
                # Close page to prevent memory leaks during long sessions
                await page.close()


    async def process_single_album(self, album_obj: Any, dry_run: bool = False) -> Optional[tuple[Any, Dict[str, Any]]]:
        """Process a single album and extract genre information (beets-compatible wrapper).

        This method maintains compatibility with beets Album objects.
        """
        try:
            # Extract data from album object (works with beets Album)
            artist = getattr(album_obj, 'albumartist', '') or getattr(album_obj, 'artist', '')
            album_name = getattr(album_obj, 'album', '')
            year = getattr(album_obj, 'year', None)

            # Get genre data for album, with artist fallback
            result = await self.get_album_genres_and_descriptors(artist, album_name, year)
            if not result:
                # Fall back to artist genres if album search fails
                result = await self.get_artist_genres_and_descriptors(artist)

            if not result:
                return None

            genres, descriptors = result

            if (genres or descriptors) and not dry_run:
                # Store genres and descriptors in the album (beets-specific)
                if genres:
                    album_obj['genres'] = '; '.join(genres)
                if descriptors:
                    album_obj['descriptors'] = '; '.join(descriptors)
                if hasattr(album_obj, 'store'):
                    album_obj.store()

            return album_obj, {'genres': genres, 'descriptors': descriptors}

        except Exception as e:
            artist = getattr(album_obj, 'albumartist', 'Unknown')
            album_name = getattr(album_obj, 'album', 'Unknown')
            self.logger.error(f"Error processing {artist} - {album_name}: {e}")
            return None

    def build_direct_url(self, artist: str, album_name: str, album_type: Literal["album", "single", "ep", "compilation"] = "album") -> str:
        """Build direct RYM URL for the given artist and album."""

        # Map album types to RYM URL paths
        type_mapping = {
            "album": "album",
            "single": "single",
            "ep": "ep",
            "compilation": "comp"
        }

        # Default to "album" for unknown types, with warning
        rym_type = type_mapping.get(album_type.lower(), "album")
        if album_type.lower() not in type_mapping:
            self.logger.warning(f"Unknown album_type '{album_type}', defaulting to 'album'")

        # Use normalize_text for URL building
        artist_clean = normalize_text(
            artist,
            remove_accents=True,
            lowercase=True,
            remove_punctuation=True
        ).replace(' ', '-')

        album_clean = normalize_text(
            album_name,
            remove_accents=True,
            lowercase=True,
            remove_punctuation=True
        ).replace(' ', '-')

        # Use HTTP since HTTPS has proxy issues
        return f"{BASE_URL}/release/{rym_type}/{artist_clean}/{album_clean}/"


    def build_artist_url(self, artist: str) -> str:
        """Build direct RYM artist URL for the given artist."""

        # Use normalize_text for URL building
        artist_clean = normalize_text(
            artist,
            remove_accents=True,
            lowercase=True,
            remove_punctuation=True
        ).replace(' ', '-')

        return f"{BASE_URL}/artist/{artist_clean}"

    def build_artist_search_url(self, artist: str) -> str:
        """Build RYM search URL for the given artist."""
        # Clean up artist name - replace non-word chars with spaces
        artist_clean = re.sub(r'[^\w\s]', ' ', artist).strip()
        # Normalize multiple spaces to single spaces
        artist_clean = re.sub(r'\s+', ' ', artist_clean)
        encoded_query = quote(artist_clean)
        return f"{BASE_URL}/search?searchtype=a&searchterm={encoded_query}"

    def _extract_artist_id_from_html(self, html: str) -> Optional[str]:
        """Extract artist ID from rym_shortcut input field in HTML."""
        try:
            # Pattern to match: <input class="rym_shortcut" ... value="[Artist1521023]">
            pattern = r'<input[^>]*class="[^"]*rym_shortcut[^"]*"[^>]*value="\[Artist(\d+)\]"[^>]*>'
            matches = re.findall(pattern, html, re.IGNORECASE)

            if matches:
                artist_id = matches[0]  # Take first match
                self.logger.debug(f"Extracted artist ID: {artist_id}")
                return artist_id
            else:
                self.logger.warning("No artist ID found in rym_shortcut input")
                return None

        except Exception as e:
            self.logger.error(f"Error extracting artist ID from HTML: {e}")
            return None

    def _extract_artist_key_from_html(self, html: str) -> Optional[str]:
        """Extract artist_key from artist page HTML.

        The artist_key is a security token required for ExpandDiscographySection requests.
        It's typically found in JavaScript variables or hidden form inputs.

        Args:
            html: Full artist page HTML

        Returns:
            Artist key string (hex), or None if not found
        """
        try:
            # Try pattern 1: JavaScript variable declaration
            # Example: var artist_key = '9da51d29402afbd91af0cfe435109516';
            pattern1 = r"var\s+artist_key\s*=\s*['\"]([a-f0-9]+)['\"]"
            matches = re.findall(pattern1, html, re.IGNORECASE)
            if matches:
                artist_key = matches[0]
                self.logger.debug(f"Extracted artist_key from JavaScript: {artist_key}")
                return artist_key

            # Try pattern 2: Hidden input field
            # Example: <input type="hidden" name="artist_key" value="9da51d29402afbd91af0cfe435109516">
            pattern2 = r'<input[^>]*name=["\']artist_key["\'][^>]*value=["\']([a-f0-9]+)["\'][^>]*>'
            matches = re.findall(pattern2, html, re.IGNORECASE)
            if matches:
                artist_key = matches[0]
                self.logger.debug(f"Extracted artist_key from input field: {artist_key}")
                return artist_key

            # Try pattern 3: Data attribute
            # Example: data-artist-key="9da51d29402afbd91af0cfe435109516"
            pattern3 = r'data-artist-key=["\']([a-f0-9]+)["\']'
            matches = re.findall(pattern3, html, re.IGNORECASE)
            if matches:
                artist_key = matches[0]
                self.logger.debug(f"Extracted artist_key from data attribute: {artist_key}")
                return artist_key

            self.logger.warning("No artist_key found in HTML")
            return None

        except Exception as e:
            self.logger.error(f"Error extracting artist_key from HTML: {e}")
            return None

    async def _get_artist_page_url(self, artist: str, page: Any) -> Optional[str]:
        """Get the artist page URL, trying direct URL first, then artist search."""
        # Try direct artist URL first
        direct_artist_url = self.build_artist_url(artist)
        self.logger.info(f"Trying direct artist URL: {direct_artist_url}")

        # Test if direct artist URL works
        html = await self.navigate_page_with_rate_limiting(direct_artist_url, page)
        if html and len(html) > 1000:  # Basic validation for substantial content
            # Quick check if this looks like an artist page (not a 404)
            soup = BeautifulSoup(html, 'html.parser')
            # Look for artist-specific elements
            if soup.find('div', class_='artist_info_main') or soup.find('div', id='discography'):
                self.logger.info(f"Direct artist URL successful: {direct_artist_url}")

                # Cache artist page content
                if self.cache_manager:
                    self.cache_manager.save_content("artist", artist, html)

                return direct_artist_url

        # Direct URL failed, try artist search
        self.logger.info(f"Direct artist URL failed, searching for artist: {artist}")
        search_url = self.build_artist_search_url(artist)

        # Use existing artist search functionality
        found_url = await self._search_artist_url(search_url, page, artist)
        if found_url:
            self.logger.info(f"Found artist via search: {found_url}")

            # Fetch and cache the artist page content
            if self.cache_manager:
                artist_html = await self.navigate_page_with_rate_limiting(found_url, page)
                if artist_html:
                    self.cache_manager.save_content("artist", artist, artist_html)

            return found_url

        self.logger.warning(f"Could not find artist page for: {artist}")
        return None

    def _parse_javascript_callback_response(self, response_text: str) -> Optional[str]:
        """Parse HTML from JavaScript callback response.

        Response format: RYMartistPage._searchCallback('stay with me', '<div class="disco_search_results">...')
        """
        try:
            # Use regex to extract the HTML from the second parameter
            import re
            pattern = r"RYMartistPage\._searchCallback\('[^']*',\s*'([^']*)'\)"
            match = re.search(pattern, response_text)

            if match:
                html_content = match.group(1)
                # Unescape any escaped quotes in the HTML
                html_content = html_content.replace("\\'", "'").replace('\\"', '"')
                self.logger.debug(f"Extracted {len(html_content)} characters of HTML from JavaScript response")
                return html_content
            else:
                self.logger.warning("Could not parse JavaScript callback response format")
                self.logger.debug(f"Response was: {response_text[:200]}...")
                return None

        except Exception as e:
            self.logger.error(f"Error parsing JavaScript callback response: {e}")
            return None

    def _get_collapsed_sections(self, html: str) -> List[str]:
        """Detect which discography sections have collapsed content that needs expanding.

        Checks each section type to determine if there are more releases that aren't
        currently visible in the HTML.

        Args:
            html: Full artist page HTML

        Returns:
            List of section type codes that need expanding (e.g., ['s', 'i', 'j'])
        """
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, 'html.parser')

            # Find the main discography div
            discography_div = soup.find('div', id='discography')
            if not discography_div:
                self.logger.debug("No discography div found in HTML")
                return []

            # Section types to check: s=album, e=ep, i=single, j=dj_mix, a=appears_on, v=va_comp, d=video
            section_types = ['s', 'e', 'i', 'j', 'a', 'v', 'd']
            collapsed_sections = []

            for section_type in section_types:
                # Check if this section exists
                section_header = discography_div.find('div', id=f'disco_header_{section_type}')
                if not section_header:
                    continue

                # Check the disco_type div - if it's empty or very small, section might be collapsed
                section_div = discography_div.find('div', id=f'disco_type_{section_type}')
                if not section_div:
                    # Header exists but no content div = needs expansion
                    collapsed_sections.append(section_type)
                    self.logger.debug(f"Section '{section_type}' has header but no content - needs expansion")
                    continue

                # Check if section div is empty or has minimal content
                section_html = str(section_div)
                if len(section_html) < 100:  # Arbitrary threshold for "empty"
                    collapsed_sections.append(section_type)
                    self.logger.debug(f"Section '{section_type}' appears empty - needs expansion")
                    continue

                # Additional check: Look for "Show X more" indicators or similar
                # (RYM may have UI elements indicating collapsed state)
                # For now, if we can't find any disco_release elements, consider it collapsed
                release_elements = section_div.find_all(class_='disco_release')
                if not release_elements:
                    collapsed_sections.append(section_type)
                    self.logger.debug(f"Section '{section_type}' has no visible releases - needs expansion")

            if collapsed_sections:
                self.logger.info(f"Found {len(collapsed_sections)} collapsed sections: {collapsed_sections}")
            else:
                self.logger.debug("No collapsed sections detected")

            return collapsed_sections

        except Exception as e:
            self.logger.error(f"Error detecting collapsed sections: {e}")
            return []

    def _parse_visible_discography(self, html: str) -> List[DiscographyCandidate]:
        """Parse all visible discography releases from artist page HTML.

        Parses releases from all section types (albums, EPs, singles, etc.) that are
        currently visible in the discography div. Does not expand collapsed sections.

        Args:
            html: Full artist page HTML

        Returns:
            List of DiscographyCandidate objects from all visible sections
        """
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, 'html.parser')

            # Find the main discography div
            discography_div = soup.find('div', id='discography')
            if not discography_div:
                self.logger.debug("No discography div found in HTML")
                return []

            # Section types to check: s=album, e=ep, i=single, j=dj_mix, a=appears_on, v=va_comp, d=video
            section_types = ['s', 'e', 'i', 'j', 'a', 'v', 'd']
            all_candidates = []

            for section_type in section_types:
                # Find the disco_type div for this section
                section_div = discography_div.find('div', id=f'disco_type_{section_type}')
                if not section_div:
                    continue

                # Parse all disco_release elements in this section
                # We can reuse the existing _parse_discography_html logic by passing section HTML
                section_html = str(section_div)
                candidates = self._parse_discography_html(section_html)

                if candidates:
                    self.logger.debug(f"Found {len(candidates)} visible releases in section '{section_type}'")
                    all_candidates.extend(candidates)

            if all_candidates:
                self.logger.info(f"Parsed {len(all_candidates)} total visible releases from artist page")
            else:
                self.logger.debug("No visible releases found in discography")

            return all_candidates

        except Exception as e:
            self.logger.error(f"Error parsing visible discography: {e}")
            return []

    def _parse_discography_html(self, html: str) -> List[DiscographyCandidate]:
        """Parse discography search results from HTML string (extracted from JavaScript response)."""
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, 'html.parser')

            # Get all disco_release items
            release_elements = soup.find_all(class_='disco_release')
            if not release_elements:
                self.logger.debug("No disco_release items found in HTML")
                return []

            self.logger.debug(f"Found {len(release_elements)} releases in discography HTML")

            candidates = []
            for i, release_elem in enumerate(release_elements):
                try:
                    # Look for disco_info section
                    disco_info = release_elem.find(class_='disco_info')
                    if not disco_info:
                        continue

                    # Find the main album link
                    album_link = disco_info.find(class_='album')
                    if not album_link:
                        continue

                    # Get album title and href
                    album_title = album_link.get_text(strip=True)
                    album_href = album_link.get('href', '')

                    # Extract year if available
                    release_year = None
                    year_elem = release_elem.find(class_='disco_year_ymd')
                    if year_elem:
                        year_text = year_elem.get_text(strip=True)
                        if year_text and year_text.isdigit():
                            release_year = int(year_text)

                    candidate = DiscographyCandidate(
                        album=album_title,
                        url=album_href,
                        year=release_year
                    )
                    candidates.append(candidate)
                    self.logger.debug(f"Parsed candidate #{i+1}: {album_title} ({release_year}) -> {album_href}")

                except Exception as e:
                    self.logger.warning(f"Error parsing discography candidate #{i+1}: {e}")
                    continue

            return candidates

        except Exception as e:
            self.logger.error(f"Error parsing discography HTML: {e}")
            return []

    async def _expand_discography_section(self, page: Any, artist_id: str, section_type: str, artist_key: str) -> List[DiscographyCandidate]:
        """Expand a collapsed discography section to get all releases.

        Makes a POST request to ExpandDiscographySection endpoint to fetch all releases
        in a specific section type (albums, EPs, singles, etc.).

        Args:
            page: Playwright page (for browser context)
            artist_id: RYM artist ID
            section_type: Section code ('s', 'e', 'i', 'j', 'a', 'v', 'd')
            artist_key: Security token from artist page

        Returns:
            List of DiscographyCandidate objects from expanded section
        """
        try:
            # Prepare form data for ExpandDiscographySection request
            form_data = {
                'artist_id': artist_id,
                'sort': 'release_date.a,title.a',
                'show_appearances': 'false',
                'type': section_type,
                'artist_key': artist_key,
                'action': 'ExpandDiscographySection',
                'rym_ajax_req': '1',
                'request_token': ''
            }

            self.logger.info(f"Expanding discography section '{section_type}' for artist_id={artist_id}")
            self.logger.debug(f"Form data: {form_data}")

            # Use fetch_ajax_post for POST request
            response_text = await self.browser_manager.fetch_ajax_post(
                page,
                f'{BASE_URL}/httprequest/ExpandDiscographySection',
                form_data
            )

            if not response_text:
                self.logger.warning(f"ExpandDiscographySection returned empty response for section '{section_type}'")
                return []

            self.logger.debug(f"ExpandDiscographySection response: {response_text[:200]}...")

            # Parse JavaScript callback response
            # Format: RYMartistPage._expandDiscographySectionCallback('j', '<html>...')
            # We can reuse _parse_javascript_callback_response which handles similar format
            html_content = self._parse_javascript_callback_response(response_text)
            if not html_content:
                self.logger.warning(f"Could not extract HTML from ExpandDiscographySection response for section '{section_type}'")
                return []

            # Parse disco_release elements from expanded HTML
            candidates = self._parse_discography_html(html_content)
            self.logger.info(f"Expanded section '{section_type}' found {len(candidates)} releases")
            return candidates

        except Exception as e:
            self.logger.error(f"Error expanding discography section '{section_type}': {e}")
            return []

    async def _search_discography_via_post(self, page: Any, artist_id: str, album: str) -> List[DiscographyCandidate]:
        """Search discography using direct POST request to FilterDiscography endpoint."""
        try:
            # Use aggressive normalization (Phase 1) for search query to avoid RYM's substring search issues
            # The returned candidates will be scored using moderate normalization (Phase 2)
            normalized_search = self._normalize_album_for_search(album)

            # Prepare form data for the POST request
            form_data = {
                'artist_id': artist_id,
                'sort': 'release_date.a,title.a',
                'searchterm': normalized_search,
                'show_appearances': 'true',
                'action': 'FilterDiscography',
                'rym_ajax_req': '1',
                'request_token': ''
            }

            self.logger.info(f"Making POST request to FilterDiscography for artist_id={artist_id}, album='{album}' (normalized: '{normalized_search}')")
            self.logger.debug(f"Form data: {form_data}")

            # Use fetch_ajax_post for POST request
            # This handles challenges, 503 errors, and IP rotation automatically
            response_text = await self.browser_manager.fetch_ajax_post(
                page,
                f'{BASE_URL}/httprequest/FilterDiscography',
                form_data
            )

            if not response_text:
                self.logger.warning("FilterDiscography POST request returned empty response")
                return []

            self.logger.debug(f"FilterDiscography response: {response_text[:200]}...")

            # Parse HTML from JavaScript response
            html_content = self._parse_javascript_callback_response(response_text)
            if not html_content:
                self.logger.warning("Could not extract HTML from JavaScript response")
                return []

            # Parse candidates from HTML
            candidates = self._parse_discography_html(html_content)
            self.logger.info(f"POST approach found {len(candidates)} discography candidates")
            return candidates

        except Exception as e:
            self.logger.error(f"Error in POST discography search: {e}")
            return []

    def _convert_arabic_to_roman(self, text: str) -> str:
        """Convert Arabic numerals to lowercase Roman numerals for better matching.

        Converts standalone Arabic numerals (1-20) to their Roman numeral equivalents
        to improve matching with RYM titles that use Roman numerals.

        Args:
            text: Text containing Arabic numerals

        Returns:
            Text with Arabic numerals converted to Roman numerals
        """
        arabic_to_roman = {
            '1': 'i', '2': 'ii', '3': 'iii', '4': 'iv', '5': 'v',
            '6': 'vi', '7': 'vii', '8': 'viii', '9': 'ix', '10': 'x',
            '11': 'xi', '12': 'xii', '13': 'xiii', '14': 'xiv', '15': 'xv',
            '16': 'xvi', '17': 'xvii', '18': 'xviii', '19': 'xix', '20': 'xx'
        }

        result = text
        # Replace in reverse order (20 before 2, 10 before 1) to avoid partial replacements
        for arabic in sorted(arabic_to_roman.keys(), key=lambda x: -int(x)):
            roman = arabic_to_roman[arabic]
            # Replace standalone numbers (word boundaries)
            result = re.sub(rf'\b{arabic}\b', roman, result)

        return result

    def _normalize_album_name(self, album_name: str) -> str:
        """Normalize album name for fuzzy matching.

        **PHASE 2 NORMALIZATION** - Used for scoring matches only.
        See class docstring for full two-phase strategy explanation.

        Applies text normalization and expands common abbreviations to improve
        matching between variants like "Vol. 2" vs "Volume 2".

        Args:
            album_name: Raw album name from RYM or metadata

        Returns:
            Normalized album name string
        """
        # First apply standard text normalization
        normalized = normalize_text(
            album_name,
            remove_accents=True,
            lowercase=True,
            remove_punctuation=False  # Keep punctuation for now, we'll handle abbreviations
        )

        # Expand common abbreviations (case-insensitive)
        # Based on music industry standards (Music Metadata Style Guide, MusicBrainz, Apple Music)
        # Organized by frequency/importance
        abbreviation_map = {
            # Tier 1: Volume/Numbering (very common)
            r'\bvol\.?\b': 'volume',
            r'\bvols\.?\b': 'volumes',
            r'\bpt\.?\b': 'part',
            r'\bpts\.?\b': 'parts',
            r'\bno\.?\b': 'number',
            r'\bnos\.?\b': 'numbers',
            r'\bch\.?\b': 'chapter',
            r'\bchs\.?\b': 'chapters',
            r'\bep\.?\b': 'episode',  # Note: Not the format "EP"

            # Tier 1: Collaboration (extremely common)
            r'\bfeat\.?\b': 'featuring',
            r'\bft\.?\b': 'featuring',  # Common alternative
            r'\bw/\b': 'with',
            r'\bvs\.?\b': 'versus',
            r'\bv\.?\b': 'versus',  # Single letter version
            r'\b&\b': 'and',
            r'\bpres\.?\b': 'presents',

            # Tier 2: Edition types (common in reissues)
            r'\bdeluxe\s+ed\.?\b': 'deluxe edition',
            r'\bltd\.?\b': 'limited',
            r'\brmx\.?\b': 'remix',
            r'\bremaster\b': 'remastered',  # Normalize to past tense
            r'\banniv\.?\b': 'anniversary',
            r'\bed\.?\b': 'edition',  # Generic edition

            # Tier 3: Recording context (moderate)
            r'\bacoustic?\b': 'acoustic',  # Handles both "acoust." and "acoustic"
            r'\binstr\.?\b': 'instrumental',
            r'\borig\.?\b': 'original',

            # Tier 4: Format/miscellaneous
            r'\bost\b': 'original soundtrack',
            r'\bo\.s\.t\.?\b': 'original soundtrack',
            r'\bcomp\.?\b': 'compilation',
            r'\bincl\.?\b': 'including',
            r'\bexcl\.?\b': 'exclusive',
            r'\bintl\.?\b': 'international',

            # Classical music (specialized)
            r'\bop\.?\b': 'opus',
            r'\borch\.?\b': 'orchestra',
            r'\bsymph\.?\b': 'symphony',
        }

        for pattern, replacement in abbreviation_map.items():
            normalized = re.sub(pattern, replacement, normalized, flags=re.IGNORECASE)

        # Convert Arabic numerals to Roman numerals for better matching with RYM titles
        normalized = self._convert_arabic_to_roman(normalized)

        # Now remove remaining punctuation and normalize spaces
        normalized = re.sub(r'[^\w\s]', ' ', normalized)
        normalized = re.sub(r'\s+', ' ', normalized).strip()

        return normalized

    def _normalize_album_for_search(self, album_name: str) -> str:
        """Normalize album for RYM search query (aggressive - strips to content words).

        **PHASE 1 NORMALIZATION** - Used for search queries only.
        See class docstring for full two-phase strategy explanation.

        Removes noise words that confuse RYM's substring search while keeping
        core identifying words for broader matches. This is used for Phase 1 (search),
        while _normalize_album_name is used for Phase 2 (scoring matches).

        Args:
            album_name: Raw album name to normalize for search

        Returns:
            Aggressively normalized album name with only content words
        """
        # First apply text normalization with punctuation removal
        normalized = normalize_text(
            album_name,
            remove_accents=True,
            lowercase=True,
            remove_punctuation=True
        )

        # Remove common noise words patterns that confuse RYM's substring search
        noise_patterns = [
            # Articles
            r'\bthe\b', r'\ba\b', r'\ban\b',
            # Volume/Part/Number
            r'\bvolume\b', r'\bvolumes\b',
            r'\bpart\b', r'\bparts\b',
            r'\bnumber\b',
            # Collaboration words
            r'\bfeaturing\b',
            r'\bwith\b',
            r'\bversus\b',
            # Conjunctions
            r'\band\b', r'\bor\b',
            # Edition/Format
            r'\bedition\b',
            r'\bdeluxe\b', r'\blimited\b',
            r'\bremaster(?:ed)?\b',
            # Prepositions
            r'\bof\b', r'\bfor\b', r'\bfrom\b', r'\bto\b',
            r'\bin\b', r'\bon\b', r'\bat\b', r'\bby\b',
        ]

        for pattern in noise_patterns:
            normalized = re.sub(pattern, ' ', normalized, flags=re.IGNORECASE)

        # Remove all Arabic numerals
        normalized = re.sub(r'\b\d+\b', ' ', normalized)

        # Remove Roman numerals (standalone)
        # Matches valid Roman numerals from I to MMMCMXCIX (3999)
        roman_pattern = r'\b(?=[MDCLXVI])(M{0,3})(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})\b'
        normalized = re.sub(roman_pattern, ' ', normalized, flags=re.IGNORECASE)

        # Normalize whitespace
        normalized = re.sub(r'\s+', ' ', normalized).strip()

        self.logger.debug(f"Search normalization: '{album_name}' -> '{normalized}'")

        return normalized

    def _score_discography_candidate(self, candidate: DiscographyCandidate, target_album: str, target_year: Optional[int] = None) -> float:
        """Calculate similarity score for discography candidate with improved fuzzy matching.

        Uses text normalization and abbreviation expansion to better match album name
        variants (e.g., "Vol." vs "Volume"). Adjusts threshold based on year match quality.

        Args:
            candidate: Discography candidate from RYM
            target_album: Target album name from metadata
            target_year: Optional target year for improved matching

        Returns:
            Similarity score from 0.0 to 1.0
        """
        def string_similarity(s1: str, s2: str) -> float:
            """Calculate string similarity using SequenceMatcher after normalization."""
            if not s1 or not s2:
                return 0.0
            # Normalize both strings before comparison
            norm_s1 = self._normalize_album_name(s1)
            norm_s2 = self._normalize_album_name(s2)
            return SequenceMatcher(None, norm_s1, norm_s2).ratio()

        # Calculate album similarity score with normalization
        album_score = string_similarity(candidate.album, target_album)

        # Year score (if available)
        year_score = 1.0  # Default if no year info
        if target_year and candidate.year:
            year_diff = abs(candidate.year - target_year)
            if year_diff == 0:
                year_score = 1.0
            elif year_diff <= 1:
                year_score = 0.9
            elif year_diff <= 2:
                year_score = 0.8
            else:
                year_score = 0.5  # Penalize large year differences

        # Combine scores (album is more important)
        final_score = album_score * 0.8 + year_score * 0.2
        return final_score


    def _score_discography_candidates(self, candidates: List[DiscographyCandidate], target_album: str, target_year: Optional[int] = None) -> Optional[str]:
        """Score discography candidates and return the best match URL.

        Note: Uses Phase 2 (moderate) normalization via _normalize_album_name()
        for accurate fuzzy matching. See class docstring for strategy details.
        """
        if not candidates:
            self.logger.debug("No candidates to score")
            return None

        # Score each candidate using simplified scoring method
        scored_candidates = []
        for candidate in candidates:
            score = self._score_discography_candidate(candidate, target_album, target_year)
            scored_candidates.append((score, candidate))

        # Sort by score (highest first), then by URL length (shortest first) for tiebreaking
        # This handles duplicates like name/, name-1/, name-2/ by picking the shortest URL
        scored_candidates.sort(key=lambda x: (-x[0], len(x[1].url)))

        # Log top candidates
        for i, (score, candidate) in enumerate(scored_candidates[:3]):
            self.logger.info(f"Discography match #{i+1}: {candidate.album} ({candidate.year}) Score: {score:.3f} URL: {candidate.url}")

        # Check threshold with year-based adjustment
        best_score, best_candidate = scored_candidates[0]
        base_threshold = self.config.matching_threshold

        # Lower threshold if year matches exactly (more confidence)
        # Reject if year differs by more than 2 (likely wrong album)
        threshold = base_threshold
        if target_year and best_candidate.year:
            year_diff = abs(best_candidate.year - target_year)
            if year_diff == 0:
                # Exact year match - lower threshold to 0.65
                threshold = min(base_threshold, 0.65)
                self.logger.debug(f"Year match ({target_year}) - lowered threshold to {threshold:.2f}")
            elif year_diff > 2:
                # Year mismatch - reject regardless of album score
                self.logger.info(f"Year mismatch: target={target_year}, candidate={best_candidate.year} - rejecting despite score {best_score:.3f}")
                return None

        if best_score < threshold:
            self.logger.info(f"Best discography match '{best_candidate.album}' score {best_score:.3f} below threshold {threshold:.3f}")
            return None

        self.logger.info(f"Selected discography match: '{best_candidate.album}' ({best_candidate.year}) with score {best_score:.3f}")

        # Return full URL
        url = best_candidate.url
        if url.startswith('/'):
            return f"{BASE_URL}{url}"
        return url

    async def _search_discography_by_artist_id(self, artist_id: str, album: str, page: Any, year: Optional[int] = None) -> Optional[str]:
        """Search artist's discography using artist ID directly (no page navigation needed).

        Args:
            artist_id: RYM artist ID
            album: Album name to search for
            page: Playwright page (for browser context)
            year: Optional album year for better matching

        Returns:
            Album URL if found, None otherwise
        """
        self.logger.info(f"Searching discography for artist_id={artist_id}, album='{album}' (year: {year})")

        try:
            # Use POST approach to search discography
            candidates = await self._search_discography_via_post(page, artist_id, album)

            # Score candidates and return best match
            if candidates:
                return self._score_discography_candidates(candidates, album, year)
            else:
                self.logger.info("Discography search returned no results")
                return None

        except Exception as e:
            self.logger.error(f"Error during discography search by artist ID: {e}")
            return None

    async def _search_artist_discography(self, artist_page_url: str, album: str, page: Any, year: Optional[int] = None) -> Optional[str]:
        """Search artist's discography using cascading strategy with progressive expansion.

        Uses a 3-tier cascading search approach:
        1. Parse visible discography HTML (fast, no extra requests)
        2. POST to FilterDiscography API (conservative server-side search)
        3. Expand collapsed sections one at a time until match found (exhaustive)

        Args:
            artist_page_url: URL to artist page on RYM
            album: Target album name to search for
            page: Playwright page (for browser context)
            year: Optional album year for better matching

        Returns:
            Album URL if found, None otherwise
        """
        self.logger.info(f"Searching discography on artist page: {artist_page_url}")
        self.logger.info(f"Looking for album: '{album}' (year: {year})")

        try:
            # Extract artist name from URL to check cache
            artist_name = None
            if "/artist/" in artist_page_url:
                url_artist = artist_page_url.split("/artist/")[-1].rstrip("/")
                if url_artist:
                    # Convert URL format back to readable name (rough approximation)
                    artist_name = url_artist.replace("-", " ").title()

            # Check artist ID cache first
            artist_id = None
            artist_key = None
            html = None

            if self.cache_manager and artist_name:
                artist_id = self.cache_manager.lookup_artist_id(artist_name)

            # Navigate to artist page if needed (for artist_id, artist_key, or HTML)
            if not artist_id:
                await self._wait_for_rate_limit()
                success = await self.browser_manager.fetch_html(page, artist_page_url)
                if not success:
                    self.logger.warning("Could not navigate to artist page, discography search failed")
                    return None

                # Wait for network to be idle
                try:
                    await page.wait_for_load_state('networkidle', timeout=10000)
                except Exception:
                    await asyncio.sleep(2)

                # Get page HTML to extract artist metadata
                html = await page.content()
                artist_id = self._extract_artist_id_from_html(html)

                if not artist_id:
                    self.logger.warning("Could not extract artist ID from page, discography search failed")
                    return None

                # Cache the artist ID for future use
                if self.cache_manager and artist_name:
                    self.cache_manager.save_artist_id(artist_name, artist_id)

                # Extract artist_key for section expansion (Tier 3)
                artist_key = self._extract_artist_key_from_html(html)

            # ========== TIER 1: Parse Visible Discography (FAST) ==========
            self.logger.info("Tier 1: Searching visible discography HTML")
            if html is None:
                # HTML wasn't loaded yet (artist_id was cached), load it now
                await self._wait_for_rate_limit()
                await self.browser_manager.fetch_html(page, artist_page_url)
                try:
                    await page.wait_for_load_state('networkidle', timeout=10000)
                except Exception:
                    await asyncio.sleep(2)
                html = await page.content()

            visible_candidates = self._parse_visible_discography(html)
            if visible_candidates:
                match_url = self._score_discography_candidates(visible_candidates, album, year)
                if match_url:
                    self.logger.info(f"Tier 1 SUCCESS: Found match in visible discography")
                    return match_url
            self.logger.info("Tier 1: No match in visible discography")

            # ========== TIER 2: FilterDiscography POST API (CONSERVATIVE) ==========
            self.logger.info("Tier 2: Trying FilterDiscography POST API")
            post_candidates = await self._search_discography_via_post(page, artist_id, album)
            if post_candidates:
                match_url = self._score_discography_candidates(post_candidates, album, year)
                if match_url:
                    self.logger.info(f"Tier 2 SUCCESS: Found match via POST API")
                    return match_url
            self.logger.info("Tier 2: No match via POST API")

            # ========== TIER 3: Expand Collapsed Sections (EXHAUSTIVE) ==========
            self.logger.info("Tier 3: Expanding collapsed sections one at a time")

            # Extract artist_key if not already extracted
            if not artist_key:
                artist_key = self._extract_artist_key_from_html(html)

            if not artist_key:
                self.logger.warning("Could not extract artist_key, cannot expand sections")
                return None

            # Get list of collapsed sections
            collapsed_sections = self._get_collapsed_sections(html)
            if not collapsed_sections:
                self.logger.info("Tier 3: No collapsed sections to expand")
                return None

            # Expand and search each section until we find a match
            for section_type in collapsed_sections:
                self.logger.info(f"Tier 3: Expanding section '{section_type}'")
                expanded_candidates = await self._expand_discography_section(
                    page, artist_id, section_type, artist_key
                )

                if expanded_candidates:
                    match_url = self._score_discography_candidates(expanded_candidates, album, year)
                    if match_url:
                        self.logger.info(f"Tier 3 SUCCESS: Found match in expanded section '{section_type}'")
                        return match_url
                    else:
                        self.logger.info(f"Tier 3: No match in section '{section_type}', trying next section")
                else:
                    self.logger.debug(f"Tier 3: Section '{section_type}' returned no candidates")

            # No match found after exhaustive search
            self.logger.info("Tier 3: No match found after expanding all collapsed sections")
            return None

        except Exception as e:
            self.logger.error(f"Error during discography search: {e}")
            return None

    def _parse_album_genres_and_descriptors(self, soup: BeautifulSoup) -> tuple[list[str], list[str]]:
        """Extract genres and descriptors from album page HTML structure."""
        genres = []
        descriptors = []

        # Fail fast: Check if this looks like a valid album page
        genre_row = soup.find('tr', class_='release_genres')
        if not genre_row:
            # No release_genres row means this isn't a valid album page
            return genres, descriptors

        # Extract all genres from the release_genres row (both primary and secondary)
        genre_links = genre_row.find_all('a', class_='genre')
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

        return genres, descriptors

    def _parse_artist_genres_and_descriptors(self, soup: BeautifulSoup) -> tuple[list[str], list[str]]:
        """Extract genres and descriptors from artist page HTML structure."""
        genres = []
        descriptors = []

        # Look for genres in artist_info_main class
        # Find the "Genres" header and extract from the following info_content div
        artist_info_main = soup.find(class_='artist_info_main')
        if artist_info_main:
            info_headers = artist_info_main.find_all('div', class_='info_hdr')
            for header in info_headers:
                if header.get_text(strip=True).lower() == 'genres':
                    # Find the next info_content div after the Genres header
                    info_content = header.find_next_sibling('div', class_='info_content')
                    if info_content:
                        genre_links = info_content.find_all('a', class_='genre')
                        for link in genre_links:
                            genre_text = link.get_text(strip=True)
                            if not genre_text:
                                continue
                            genres.append(genre_text)
                    break

        return genres, descriptors

    def _extract_genres_from_html(self, html: str, content_type: Literal["album", "artist"] = "album") -> tuple[list[str], list[str]]:
        """Extract genre information and descriptors from RYM page HTML."""
        if not html:
            return [], []

        try:
            soup = BeautifulSoup(html, 'html.parser')

            # Use appropriate parser based on content type
            if content_type == "album":
                genres, descriptors = self._parse_album_genres_and_descriptors(soup)
            elif content_type == "artist":
                genres, descriptors = self._parse_artist_genres_and_descriptors(soup)
            else:
                self.logger.error(f"Unknown content_type: {content_type}")
                return [], []
        except (AttributeError, ValueError, TypeError) as e:
            self.logger.error(f"HTML parsing error: {e}")
            return [], []
        except Exception as e:
            self.logger.error(f"Unexpected error extracting genres from HTML: {e}")
            return [], []

        # Expand genres with parent genres if enabled
        final_genres = _deduplicate_list(genres)
        if self.genre_manager and self.config.expand_parent_genres and final_genres:
            try:
                # Ensure genre manager has loaded data
                if not self.genre_manager._loaded:
                    self.logger.debug("Genre manager not loaded, attempting to load hierarchy data")
                    if not self.genre_manager.load_hierarchy_data():
                        self.logger.warning("Could not load genre hierarchy data, parent genre expansion disabled")
                        return final_genres, _deduplicate_list(descriptors)

                expanded_genres = self.genre_manager.expand_genres_with_parents(final_genres)
                original_count = len(final_genres)
                final_genres = _deduplicate_list(expanded_genres)
                expanded_count = len(final_genres)

                if expanded_count > original_count:
                    self.logger.debug(f"Expanded {original_count} original genres to {expanded_count} total genres (added {expanded_count - original_count} parent genres)")
                else:
                    self.logger.debug(f"No parent genres added for {original_count} genres")

            except (FileNotFoundError, PermissionError, OSError) as e:
                self.logger.warning(f"File system error during genre expansion: {e}")
                # Continue with original genres if expansion fails
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                self.logger.warning(f"Content parsing error during genre expansion: {e}")
                # Continue with original genres if expansion fails
            except Exception as e:
                self.logger.warning(f"Unexpected error during genre expansion: {e}")
                # Continue with original genres if expansion fails

        return final_genres, _deduplicate_list(descriptors)



    async def _search_artist_url(self, search_url: str, page: Any, artist: str) -> Optional[str]:
        """Search for artist URL on RYM search page using fuzzy matching."""
        html = await self.navigate_page_with_rate_limiting(search_url, page)
        if not html:
            return None

        try:
            soup = BeautifulSoup(html, 'html.parser')

            # For artist search, look for results with class="searchpage" that link to artists
            artist_links = soup.find_all('a', class_='searchpage', href=re.compile(r'/artist/'))

            if not artist_links:
                self.logger.debug("No artist links found in search results")
                return None

            # Look for exact match using normalized text comparison
            normalized_artist = normalize_text(artist, remove_accents=True, lowercase=True)

            for link in artist_links:
                link_text = link.get_text(strip=True)
                normalized_link_text = normalize_text(link_text, remove_accents=True, lowercase=True)
                if normalized_link_text == normalized_artist:
                    self.logger.info(f"Found exact artist match: '{link_text}' matches '{artist}'")
                    relative_url = link['href']
                    return f"{BASE_URL}{relative_url}"

            # No exact match found
            self.logger.info(f"No exact match found for artist '{artist}' in search results")

        except Exception as e:
            self.logger.error(f"Error parsing artist search results: {e}")

        return None


    async def _wait_for_rate_limit(self) -> None:
        """Wait if needed to respect rate limiting with optional jitter."""
        if self.config.min_request_interval <= 0:
            return  # Rate limiting disabled

        if self._last_request_time is None:
            return  # First request

        elapsed = time.time() - self._last_request_time
        base_delay = self.config.min_request_interval

        if self.config.humanize_request_interval:
            # Add ±25% jitter to make requests look more human
            jitter = random.uniform(-0.25, 0.25) * base_delay
            delay_needed = base_delay + jitter
        else:
            delay_needed = base_delay

        wait_time = delay_needed - elapsed
        if wait_time > 0:
            self.logger.debug(f"Rate limiting: waiting {wait_time:.2f}s before next request")
            await asyncio.sleep(wait_time)

    def _update_request_time(self) -> None:
        """Update the timestamp of the last request."""
        self._last_request_time = time.time()

    def _parse_genre_ids_from_html(self, html: str) -> List[str]:
        """Extract genre IDs from RYM genres page HTML."""
        try:
            soup = BeautifulSoup(html, 'html.parser')
            genre_list = soup.find('ul', class_='page_genre_index_hierarchy')

            if not genre_list:
                self.logger.warning("Could not find genre hierarchy list in page")
                return []

            genre_items = genre_list.find_all('li')
            if not genre_items:
                self.logger.warning("No genre items found in hierarchy list")
                return []

            # Extract genre IDs from li element IDs
            genre_ids = []
            for item in genre_items:
                genre_id = item.get('id', '')
                if not genre_id:
                    continue

                parts = genre_id.split('_')
                if len(parts) > 1:
                    genre_ids.append(parts[-1])
                else:
                    self.logger.warning(f"Unexpected genre id format: {genre_id}")

            return genre_ids

        except Exception as e:
            self.logger.error(f"Error parsing genre IDs from HTML: {e}")
            return []

    async def _fetch_single_genre_data(self, page: Any, genre_id: str) -> Optional[Dict[str, Any]]:
        """Fetch hierarchy data for a single genre ID from the API."""
        api_url = f"{BASE_URL}/api/1/genre/hierarchy/{genre_id}/"

        try:
            # Use the robust navigate_page_with_rate_limiting method with JSON response type
            json_data = await self.navigate_page_with_rate_limiting(api_url, page, response_type='json')

            if json_data is None:
                self.logger.warning(f"Failed to fetch genre hierarchy data for {genre_id}")
                return None

            if not json_data:
                self.logger.warning(f"Empty genre hierarchy data for {genre_id}")
                return None

            return json_data

        except Exception as e:
            self.logger.warning(f"Error fetching genre {genre_id}: {e}")
            return None

    async def _collect_all_genre_data(self, page: Any, genre_ids: List[str]) -> Dict[str, Any]:
        """Collect hierarchy data for all genre IDs with progress tracking."""
        genre_data = {}
        total_genres = len(genre_ids)

        self.logger.info(f"Found {total_genres} top-level genres to fetch")

        for i, genre_id in enumerate(genre_ids):
            self.logger.info(f"Fetching genre hierarchy data ({i+1}/{total_genres}): {genre_id}")

            json_data = await self._fetch_single_genre_data(page, genre_id)
            if json_data:
                # Store by genre URL for consistent lookup
                genre_url = json_data.get('url', f'unknown_{genre_id}')
                genre_data[genre_url] = json_data

        if not genre_data:
            self.logger.error("No genre data collected")
            return {}

        self.logger.info(f"Successfully collected {len(genre_data)} genre hierarchies")
        return genre_data

    def _resolve_genre_output_path(self) -> str:
        """Determine where to save the genre hierarchy JSON file."""
        if hasattr(self, 'cache_manager') and self.cache_manager:
            output_path = self.cache_manager.cache_dir / "genre_hierarchy.json"
            return str(output_path)
        else:
            # Fallback to config cache_dir
            import os
            cache_dir = getattr(self.config, 'cache_dir', '.rym_cache')
            if not os.path.isabs(cache_dir):
                cache_dir = os.path.abspath(cache_dir)
            os.makedirs(cache_dir, exist_ok=True)
            return os.path.join(cache_dir, "genre_hierarchy.json")

    def _preprocess_genre_data_for_name_lookup(self, raw_genre_data: Dict[str, Any]) -> Dict[str, Any]:
        """Convert URL-based genre data to name-based with preprocessed depths and parent lists."""
        processed_data = {}

        def process_genre_recursively(genre_data: Dict[str, Any], depth: int, parent_names: List[str]) -> None:
            """Recursively process genres to build name-based lookup with depth."""
            genre_name = genre_data.get('name_display')
            if not genre_name:
                return

            # Store by name with depth and parent names
            processed_data[genre_name] = {
                'name': genre_name,
                'depth': depth,
                'parents': parent_names.copy(),
                'url': genre_data.get('url', ''),
                'genre_id': genre_data.get('genre_id'),
                'description_short': genre_data.get('description_short', ''),
            }

            # Process children with increased depth
            children = genre_data.get('children', [])
            new_parent_names = parent_names + [genre_name]

            for child_data in children:
                process_genre_recursively(child_data, depth + 1, new_parent_names)

        # Process all top-level genres (depth 0, no parents)
        for genre_data in raw_genre_data.values():
            process_genre_recursively(genre_data, 0, [])

        return processed_data

    def _save_genre_data_to_json(self, data: Dict[str, Any], output_path: str) -> bool:
        """Save genre hierarchy data to JSON file."""
        import json
        try:
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            self.logger.info(f"Wrote genre hierarchy data to {output_path}")
            return True
        except Exception as e:
            self.logger.error(f"Error writing genre hierarchy to file: {e}")
            return False

    async def _scrape_genre_hierarchy(self) -> Optional[str]:
        """Scrape the complete genre hierarchy from RYM and save to JSON file."""
        page = await self._create_page()  # Use the same method that inherits cookies from browser context
        try:
            # Step 1: Fetch the genres page
            await self._wait_for_rate_limit()
            url = f"{BASE_URL}/genres"
            self.logger.info(f"Fetching genre hierarchy from {url}")
            html = await self.browser_manager.fetch_html(page, url)
            if not html:
                self.logger.error("Failed to navigate to genres page")
                return None
            if not html or len(html) < 1000:
                self.logger.warning("Genre hierarchy page content too short or empty")
                return None

            # Step 2: Parse genre IDs from HTML
            genre_ids = self._parse_genre_ids_from_html(html)
            if not genre_ids:
                self.logger.warning("No genre IDs found in HTML")
                return None

            # Step 3: Collect all genre hierarchy data
            raw_genre_data = await self._collect_all_genre_data(page, genre_ids)
            if not raw_genre_data:
                return None

            # Step 3.5: Preprocess data for name-based lookup with depths
            processed_genre_data = self._preprocess_genre_data_for_name_lookup(raw_genre_data)
            self.logger.info(f"Preprocessed {len(processed_genre_data)} genres with depth information")

            # Step 4: Save to file
            output_path = self._resolve_genre_output_path()
            if self._save_genre_data_to_json(processed_genre_data, output_path):
                return output_path

            return None

        except Exception as e:
            self.logger.error(f"Error during genre hierarchy scraping: {e}")
            return None
        finally:
            await page.close()
