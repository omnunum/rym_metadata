"""Core scraping functionality for RYM metadata extraction."""

import asyncio
import json
import logging
import random
import re
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Optional, Dict, Any, List, Literal
from urllib.parse import quote

from bs4 import BeautifulSoup
from camoufox import AsyncCamoufox
from tenacity import retry, stop_after_attempt, wait_exponential

from .content_cache_manager import ContentCacheManager
from .session_manager import ProxySessionManager
from .browser import BrowserManager
from .text_utils import normalize_text
from .genre_manager import GenreHierarchyManager

BASE_URL = "https://rateyourmusic.com"


@dataclass
class DiscographyCandidate:
    """Container for discography search candidate."""
    album: str
    year: Optional[int]
    url: str

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
    """Handles core scraping operations for RYM album data."""

    def __init__(self, config: Any, cache_manager: Optional[ContentCacheManager] = None,
                 session_manager: Optional[ProxySessionManager] = None,
                 browser_manager: Optional[BrowserManager] = None) -> None:
        self.config = config
        self.cache_manager = cache_manager
        self.session_manager = session_manager
        self.browser_manager = browser_manager
        self.logger = logging.getLogger(__name__)

        # Rate limiting
        self._last_request_time: Optional[float] = None

        # Browser state management - only browser context, pages created as needed
        self._browser = None
        self._browser_context = None
        self._cf_challenge_solved = False

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

            # Solve Cloudflare challenge once at startup (with automatic retries)
            if not await self._solve_cloudflare_challenge_once():
                self.logger.error("Failed to solve Cloudflare challenge after 3 attempts - browser session may not work properly")

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

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=5, min=5, max=15))
    async def _solve_cloudflare_challenge_once(self) -> bool:
        """Solve Cloudflare challenge and store session state. Returns True on success."""
        if self._cf_challenge_solved:
            return True  # Already solved

        # Get browser context for all cookie operations
        browser_context = self._browser.contexts[0] if self._browser.contexts else await self._browser.new_context()

        # Apply any existing session cookies to the browser context first
        if self.session_manager and self.session_manager.is_session_valid():
            await self.browser_manager.apply_session_cookies_to_context(browser_context)
            self.logger.debug("Applied existing session cookies to browser context")

        # Create a temporary page to solve the challenge
        test_page = await browser_context.new_page()
        try:
            # Set up basic resource blocking
            await self.browser_manager.setup_resource_blocking(test_page)

            # Try a simple RYM page to check if cookies are valid
            await test_page.goto(BASE_URL, wait_until='domcontentloaded')

            # Check if a Cloudflare challenge is actually present
            challenge_present = await self._detect_cloudflare_challenge(test_page)

            if challenge_present:
                self.logger.info("Cloudflare challenge detected, attempting to solve...")
                challenge_solved = await self.browser_manager.solve_cloudflare_challenge(test_page, BASE_URL)
                if not challenge_solved:
                    self.logger.warning("Cloudflare challenge solving failed")
                    raise Exception("Challenge solving failed, retrying...")
                self.logger.info("Cloudflare challenge solved successfully")
            else:
                self.logger.info("No Cloudflare challenge detected, cookies are still valid")
            self._cf_challenge_solved = True

            # Extract and save cookies for future use
            if self.session_manager:
                cookies = await self.browser_manager._extract_cookies(test_page)
                if cookies:
                    self.session_manager.set_cookies(cookies)
                    self.logger.debug("Saved Cloudflare session cookies")

                    # Apply fresh cookies to browser context (they should already be there, but ensure consistency)
                    await self.browser_manager.apply_session_cookies_to_context(browser_context)
                    self.logger.info("Ensured Cloudflare session cookies are applied to browser context")

            return True

        finally:
            await test_page.close()

    async def _detect_cloudflare_challenge(self, page: Any) -> bool:
        """Detect if current page is showing a Cloudflare challenge."""
        try:
            page_content = await page.content()
            challenge_indicators = ['cloudflare', 'just a moment', 'checking your browser', 'ray id']

            content_lower = page_content.lower()
            for indicator in challenge_indicators:
                if indicator in content_lower:
                    self.logger.debug(f"Detected Cloudflare challenge indicator: '{indicator}'")
                    return True

            self.logger.debug("No Cloudflare challenge indicators found in page content")
            return False

        except Exception as e:
            self.logger.warning(f"Error detecting challenge: {e}")
            return True  # Assume challenge present if we can't detect

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
                self._cf_challenge_solved = False

    async def _handle_server_overload_rotation(self, response: Any, page: Any) -> bool:
        """Handle 503/522 status with IP rotation and cookie clearing.

        Returns:
            True if rotated successfully and should continue retry
            False if no more ports available (stop retrying)
            None if rotation not enabled (fall through to normal retry)
        """
        if response.status not in [503, 522]:
            return None

        error_type = "server overload" if response.status == 503 else "connection timeout"

        if not (self.session_manager and self.config.auto_rotate_on_failure):
            self.logger.warning(f"{response.status} {error_type} detected but auto_rotate_on_failure is disabled")
            return None

        self.logger.warning(f"{response.status} {error_type} detected, rotating IP")
        self.session_manager.mark_port_blocked()

        if self.session_manager.rotate_port():
            self.logger.info("Rotated to new port, clearing cookies and re-solving challenge")
            browser_context = page.context
            await browser_context.clear_cookies()
            self._cf_challenge_solved = False

            # Immediately re-solve the challenge with the new IP
            if await self._solve_cloudflare_challenge_once():
                self.logger.info("Successfully re-solved Cloudflare challenge after IP rotation")
                return True
            else:
                self.logger.error("Failed to re-solve Cloudflare challenge after IP rotation")
                return False
        else:
            self.logger.error("No more ports available")
            return False

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

    async def _navigate_to_url(self, url: str, page: Any) -> bool:
        """Navigate page to URL (needed for JavaScript interaction)."""
        try:
            # Apply rate limiting before making request
            await self._wait_for_rate_limit()

            # Navigate to URL
            await page.goto(url, wait_until='domcontentloaded')

            # Wait for network to be idle
            try:
                await page.wait_for_load_state('networkidle', timeout=10000)
            except Exception:
                # Fallback if networkidle fails
                await asyncio.sleep(2)

            # Update request time for rate limiting
            self._update_request_time()

            # Increment request count for successful request
            if self.session_manager:
                self.session_manager.increment_request_count()

            return True

        except Exception as e:
            error_msg = str(e)
            self.logger.debug(f"Failed to navigate to {url}: {error_msg}")
            return False

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=30))
    async def _fetch_url(self, url: str, page: Any, response_type: str = 'html') -> Optional[Any]:
        """Fetch URL with automatic retries. Returns HTML string or parsed JSON.

        Args:
            url: URL to fetch
            page: Playwright page object
            response_type: 'html' for HTML content (default), 'json' for JSON API responses

        Returns:
            HTML string for response_type='html', parsed JSON for response_type='json'
        """
        self.logger.debug(f"Fetching URL: {url} (type: {response_type})")

        # Apply rate limiting before making request
        await self._wait_for_rate_limit()

        if response_type == 'json':
            # For JSON API requests, use the page context's request API
            response = await page.request.get(url)

            # Check if this is a 503/522 that needs IP rotation (before checking status)
            rotation_result = await self._handle_server_overload_rotation(response, page)
            if rotation_result is True:
                # IP rotated and cookies cleared, let @retry handle the retry
                raise Exception(f"Server overload {response.status}, IP rotated - retrying")
            elif rotation_result is False:
                # No more ports available
                raise Exception(f"Server overload {response.status}, no more ports available")

            if response.status == 200:
                json_data = await response.json()
                # Update request time for rate limiting
                self._update_request_time()
                # Increment request count for successful request
                if self.session_manager:
                    self.session_manager.increment_request_count()
                return json_data
            else:
                # Other status codes - let @retry handle it
                raise Exception(f"JSON request failed with status {response.status}")

        else:
            # HTML fetching logic
            await page.goto(url, wait_until='domcontentloaded')

            # Wait for network to be idle
            try:
                await page.wait_for_load_state('networkidle', timeout=10000)
            except Exception:
                # Fallback if networkidle fails
                await asyncio.sleep(2)

            # Get page source
            html = await page.content()

            # Basic validation
            if not html or len(html) < 1000:
                raise Exception(f"Got minimal content: {len(html) if html else 0} chars")

            # Update request time for rate limiting
            self._update_request_time()

            # Increment request count for successful request
            if self.session_manager:
                self.session_manager.increment_request_count()
            return html


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

            html = await self._fetch_url(direct_url, page)
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
                        html = await self._fetch_url(album_url, page)
                        genres, descriptors = self._extract_genres_from_html(html) if html else ([], [])

                        # Cache successful result
                        if html and (genres or descriptors) and self.cache_manager:
                            self.cache_manager.save_content("release", artist, html, album)

                # If artist ID cache miss or discography search failed, try full artist page approach
                if not genres:
                    self.logger.info(f"Artist ID cache miss or search failed, trying full artist page approach for {artist} - {album}")

                    artist_page_url = await self._get_artist_page_url(artist, page)

                    if artist_page_url:
                        album_url = await self._search_artist_discography(artist_page_url, album, page, year)

                        if album_url:
                            html = await self._fetch_url(album_url, page)
                            genres, descriptors = self._extract_genres_from_html(html) if html else ([], [])

                            # Cache successful result
                            if html and (genres or descriptors) and self.cache_manager:
                                self.cache_manager.save_content("release", artist, html, album)

            # Return results or None
            if genres or descriptors:
                return genres, descriptors
            else:
                self.logger.info(f"No genres found for {artist} - {album}")
                return None

        except Exception as e:
            self.logger.error(f"Error processing {artist} - {album}: {e}")
            return None
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

            html = await self._fetch_url(direct_url, page)
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
                    html = await self._fetch_url(artist_url, page)
                    genres, descriptors = self._extract_genres_from_html(html, "artist") if html else ([], [])

                    # Cache successful result
                    if html and (genres or descriptors) and self.cache_manager:
                        self.cache_manager.save_content("artist", artist, html)

            # Return results or None
            if genres or descriptors:
                return genres, descriptors
            else:
                self.logger.info(f"No genres found for artist {artist}")
                return None

        except Exception as e:
            self.logger.error(f"Error processing artist {artist}: {e}")
            return None
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

    async def _get_artist_page_url(self, artist: str, page: Any) -> Optional[str]:
        """Get the artist page URL, trying direct URL first, then artist search."""
        # Try direct artist URL first
        direct_artist_url = self.build_artist_url(artist)
        self.logger.info(f"Trying direct artist URL: {direct_artist_url}")

        # Test if direct artist URL works
        html = await self._fetch_url(direct_artist_url, page)
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
                artist_html = await self._fetch_url(found_url, page)
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

            self.logger.info(f"Found {len(release_elements)} releases in discography HTML")

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

    async def _search_discography_via_post(self, browser_context: Any, artist_id: str, album: str) -> List[DiscographyCandidate]:
        """Search discography using direct POST request to FilterDiscography endpoint."""
        try:
            # Prepare form data for the POST request
            form_data = {
                'artist_id': artist_id,
                'sort': 'release_date.a,title.a',
                'searchterm': album,
                'show_appearances': 'true',
                'action': 'FilterDiscography',
                'rym_ajax_req': '1',
                'request_token': ''
            }

            self.logger.info(f"Making POST request to FilterDiscography for artist_id={artist_id}, album='{album}'")
            self.logger.debug(f"Form data: {form_data}")

            # Use browser context's request API to inherit cookies
            api_request = browser_context.request
            response = await api_request.post(
                f'{BASE_URL}/httprequest/FilterDiscography',
                form=form_data
            )

            if response.status != 200:
                self.logger.warning(f"FilterDiscography POST request failed with status {response.status}")
                return []

            # Get response text
            response_text = await response.text()
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

    def _score_discography_candidate(self, candidate: DiscographyCandidate, target_album: str, target_year: Optional[int] = None) -> float:
        """Calculate similarity score for discography candidate (album name + year only)."""
        def string_similarity(s1: str, s2: str) -> float:
            """Calculate string similarity using SequenceMatcher."""
            if not s1 or not s2:
                return 0.0
            return SequenceMatcher(None, s1.lower().strip(), s2.lower().strip()).ratio()

        # Calculate album similarity score
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
        """Score discography candidates and return the best match URL."""
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

        # Check threshold
        best_score, best_candidate = scored_candidates[0]
        threshold = self.config.matching_threshold

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
            browser_context = page.context
            candidates = await self._search_discography_via_post(browser_context, artist_id, album)

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
        """Search artist's discography for the album using direct POST request."""
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
            if self.cache_manager and artist_name:
                artist_id = self.cache_manager.lookup_artist_id(artist_name)

            if not artist_id:
                # Navigate to the artist page to extract artist_id
                await self._wait_for_rate_limit()
                await page.goto(artist_page_url, wait_until='domcontentloaded')

                # Wait for network to be idle
                try:
                    await page.wait_for_load_state('networkidle', timeout=10000)
                except Exception:
                    await asyncio.sleep(2)

                # Debug: Check current page URL and title to ensure we're where we expect
                current_url = page.url
                try:
                    page_title = await page.title()
                    self.logger.info(f"After navigation - URL: {current_url}, Title: {page_title}")
                except:
                    self.logger.info(f"After navigation - URL: {current_url}")

                # Get page HTML to extract artist ID
                html = await page.content()
                artist_id = self._extract_artist_id_from_html(html)

                if not artist_id:
                    self.logger.warning("Could not extract artist ID from page, discography search failed")
                    return None

                # Cache the artist ID for future use
                if self.cache_manager and artist_name:
                    self.cache_manager.save_artist_id(artist_name, artist_id)

            # Use the extracted discography search method
            return await self._search_discography_by_artist_id(artist_id, album, page, year)

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
        html = await self._fetch_url(search_url, page)
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
            # Use the robust _fetch_url method with JSON response type
            json_data = await self._fetch_url(api_url, page, response_type='json')

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
            await page.goto(url, wait_until='domcontentloaded')

            html = await page.content()
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
