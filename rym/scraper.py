"""Core scraping functionality for RYM metadata extraction."""

import asyncio
import logging
import re
from typing import Optional, Dict
from urllib.parse import quote

from bs4 import BeautifulSoup
from beets.library import Album

from .cache_manager import HtmlCacheManager
from .session_manager import ProxySessionManager
from .browser import BrowserManager
from .config import ProxyConfig


class RYMScraper:
    """Handles core scraping operations for RYM album data."""

    def __init__(self, config, proxy_config: ProxyConfig, cache_manager: Optional[HtmlCacheManager] = None,
                 session_manager: Optional[ProxySessionManager] = None,
                 browser_manager: Optional[BrowserManager] = None):
        self.config = config
        self.proxy_config = proxy_config
        self.cache_manager = cache_manager
        self.session_manager = session_manager
        self.browser_manager = browser_manager
        self.logger = logging.getLogger(__name__)

    async def process_single_album(self, album: Album, page, dry_run: bool = False):
        """Process a single album and extract genre information using async captcha solving."""
        try:
            # Try direct URL first
            direct_url = self.build_direct_url(album.albumartist, album.album)
            self.logger.debug("Trying direct URL: %s", direct_url)

            # Test if direct URL works
            genre_data = await self.extract_genres_from_url(direct_url, page)
            genres = genre_data.get('genres', [])
            descriptors = genre_data.get('descriptors', [])

            # If direct URL fails, fall back to search
            if not genres:
                self.logger.debug(f"Direct URL failed, searching for {album.albumartist} - {album.album}")
                search_url = self.build_search_url(album.albumartist, album.album)
                album_year = getattr(album, 'year', None)

                # Import here to avoid circular import
                from .search import RYMSearchEngine
                search_engine = RYMSearchEngine(self)
                album_url = await search_engine.search_album_url(search_url, page, album.albumartist, album.album, album_year)

                if not album_url:
                    self.logger.debug(f"No RYM page found for {album.albumartist} - {album.album}")
                    return None

                # Fetch album page and extract genres
                genre_data = await self.extract_genres_from_url(album_url, page)
                genres = genre_data.get('genres', [])
                descriptors = genre_data.get('descriptors', [])

            if (genres or descriptors) and not dry_run:
                # Store genres and descriptors in the album
                if genres:
                    album['rym_genres'] = '; '.join(genres)
                if descriptors:
                    album['rym_descriptors'] = '; '.join(descriptors)
                album.store()

            return album, {'genres': genres, 'descriptors': descriptors}

        except Exception as e:
            self.logger.error(f"Error processing {album.albumartist} - {album.album}: {e}")
            return None

    def build_direct_url(self, artist: str, album_name: str) -> str:
        """Build direct RYM URL for the given artist and album."""
        # Clean and normalize for URL
        artist_clean = re.sub(r'[^\w\s]', '', artist.lower()).strip()
        artist_clean = re.sub(r'\s+', '-', artist_clean)

        album_clean = re.sub(r'[^\w\s]', '', album_name.lower()).strip()
        album_clean = re.sub(r'\s+', '-', album_clean)

        # Use HTTP since HTTPS has proxy issues
        return f"http://rateyourmusic.com/release/album/{artist_clean}/{album_clean}/"

    def build_search_url(self, artist: str, album_name: str) -> str:
        """Build RYM search URL for the given artist and album."""
        # Clean up artist and album names
        artist_clean = re.sub(r'[^\w\s]', ' ', artist).strip()
        album_clean = re.sub(r'[^\w\s]', ' ', album_name).strip()

        # Build search query
        query = f"{artist_clean} {album_clean}".strip()
        encoded_query = quote(query)

        return f"http://rateyourmusic.com/search?searchtype=l&searchterm={encoded_query}"

    async def extract_genres_from_url(self, url: str, page) -> dict:
        """Extract genre information and descriptors from an RYM album page using async."""
        html = await self.fetch_url_with_retry(url, page)
        if not html:
            return {'genres': [], 'descriptors': []}

        try:
            soup = BeautifulSoup(html, 'html.parser')
            genres = []
            descriptors = []

            # Look for the specific release_genres row
            genre_row = soup.find('tr', class_='release_genres')
            if genre_row:
                # Find all genre links within the release_pri_genres span
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

            # Fallback to broader search if no genres found
            if not genres:
                genre_links = soup.find_all('a', class_='genre')
                for link in genre_links:
                    genre_text = link.get_text(strip=True)
                    if genre_text and len(genre_text) > 1:
                        genres.append(genre_text)

            # Remove duplicates while preserving order
            def deduplicate(items):
                seen = set()
                unique_items = []
                for item in items:
                    if item not in seen:
                        seen.add(item)
                        unique_items.append(item)
                return unique_items

            return {
                'genres': deduplicate(genres),
                'descriptors': deduplicate(descriptors)
            }

        except Exception as e:
            self.logger.error(f"Error extracting genres from {url}: {e}")
            return {'genres': [], 'descriptors': []}

    async def fetch_url_with_retry(self, url: str, page) -> Optional[str]:
        """Fetch URL using AsyncCamoufox with automatic captcha solving and session management."""
        # Check cache first
        if self.cache_manager:
            cached_html = self.cache_manager.get_cached_html(url)
            if cached_html:
                return cached_html

        max_retries = self.config['max_retries'].get()

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
                    if self.session_manager:
                        self.logger.warning("Proxy error detected, marking port as blocked")
                        self.session_manager.mark_port_blocked()
                        if self.session_manager.rotate_port():
                            self.logger.info("Rotated to new port, will retry")
                            # Port rotation handled by session manager
                            continue
                        else:
                            self.logger.error("No more ports available")
                            return None

                # Check for other errors
                if "CERTIFICATE" in error_msg.upper():
                    self.logger.warning("SSL certificate issue - may need custom certificate configuration")

            if attempt < max_retries:
                retry_delay = self.config['retry_delay'].get()
                await asyncio.sleep(retry_delay * (attempt + 1))  # Exponential backoff

        self.logger.warning(f"Failed to fetch {url} after {max_retries + 1} attempts")
        return None