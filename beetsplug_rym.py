"""RateYourMusic genre scraper plugin for beets.

This plugin scrapes genre information from RateYourMusic using the Bright Data API
to bypass Cloudflare protection. It processes albums asynchronously with retry logic.
"""

import os
import re
import asyncio
import aiohttp
import logging
from urllib.parse import quote
from typing import List, Dict, Optional, Set
from bs4 import BeautifulSoup

from beets import plugins, ui, config
from beets.library import Album


class RYMPlugin(plugins.BeetsPlugin):
    """Plugin to fetch genre information from RateYourMusic."""

    def __init__(self):
        super().__init__()

        self.config.add({
            'brightdata_token': None,
            'max_retries': 3,
            'retry_delay': 2.0,
            'concurrent_requests': 5,
            'request_timeout': 60,
            'auto_tag': False,
        })

        # Get token from environment variable if not in config
        self.token = (self.config['brightdata_token'].get() or
                     os.environ.get('BRIGHTDATA_TOKEN'))

        if not self.token:
            self._log.warning("No Bright Data token found. Set BRIGHTDATA_TOKEN env var or brightdata_token config.")

        self._bd_session_id = None

    def commands(self):
        """Register the rym command."""
        cmd = ui.Subcommand('rym', help='fetch genre info from RateYourMusic')
        cmd.parser.add_option('-f', '--force', action='store_true',
                            help='re-fetch genre info even if already present')
        cmd.parser.add_option('-d', '--dry-run', action='store_true',
                            help='show what would be done without making changes')
        cmd.parser.add_option('--debug', action='store_true',
                            help='enable debug logging')
        cmd.func = self.rym_command
        return [cmd]

    def rym_command(self, lib, opts, args):
        """Handle the rym command."""
        if opts.debug:
            self._log.setLevel(logging.DEBUG)
            logging.basicConfig(level=logging.DEBUG)

        if not self.token:
            ui.print_("Error: No Bright Data token configured")
            return

        query = ui.decargs(args)
        albums = lib.albums(query)

        if not albums:
            ui.print_("No albums found matching query")
            return

        ui.print_(f"Processing {len(albums)} album(s)...")

        # Run async processing
        asyncio.run(self._process_albums(albums, opts.force, opts.dry_run))

    async def _process_albums(self, albums: List[Album], force: bool = False, dry_run: bool = False):
        """Process albums asynchronously with concurrency control."""

        # Filter albums that need processing
        albums_to_process = []
        for album in albums:
            if force or not album.get('rym_genres'):
                albums_to_process.append(album)
            else:
                ui.print_(f"Skipping {album.albumartist} - {album.album} (already has RYM genres)")

        if not albums_to_process:
            ui.print_("No albums need processing")
            return

        # Create semaphore for concurrent requests
        semaphore = asyncio.Semaphore(self.config['concurrent_requests'].get())

        # Create aiohttp session with configured timeout
        timeout = aiohttp.ClientTimeout(total=self.config['request_timeout'].get())
        async with aiohttp.ClientSession(timeout=timeout) as session:

            # Create tasks for all albums
            tasks = [
                self._process_album_with_semaphore(semaphore, album, session, dry_run)
                for album in albums_to_process
            ]

            # Execute with progress updates
            for i, task in enumerate(asyncio.as_completed(tasks), 1):
                try:
                    result = await task
                    if result:
                        album, genres = result
                        ui.print_(f"[{i}/{len(tasks)}] {album.albumartist} - {album.album}: {', '.join(genres)}")
                    else:
                        ui.print_(f"[{i}/{len(tasks)}] Failed to process album")
                except Exception as e:
                    ui.print_(f"[{i}/{len(tasks)}] Error: {e}")

    async def _process_album_with_semaphore(self, semaphore: asyncio.Semaphore,
                                          album: Album, session: aiohttp.ClientSession, dry_run: bool = False):
        """Process a single album with semaphore control."""
        async with semaphore:
            return await self._process_single_album(album, session, dry_run)

    async def _process_single_album(self, album: Album, session: aiohttp.ClientSession, dry_run: bool = False):
        """Process a single album and extract genre information."""
        try:
            # Try direct URL first
            direct_url = self._build_direct_url(album.albumartist, album.album)
            self._log.debug(f"Trying direct URL: {direct_url}")

            # Test if direct URL works
            genres = await self._extract_genres_from_url(direct_url, session)
            album_url = direct_url

            # If direct URL fails, fall back to search
            if not genres:
                self._log.debug(f"Direct URL failed, searching for {album.albumartist} - {album.album}")
                search_url = self._build_search_url(album.albumartist, album.album)
                album_url = await self._search_album_url(search_url, session)

                if not album_url:
                    self._log.debug(f"No RYM page found for {album.albumartist} - {album.album}")
                    return None

                # Fetch album page and extract genres
                genres = await self._extract_genres_from_url(album_url, session)

            if genres and not dry_run:
                # Store genres in the album
                album['rym_genres'] = '; '.join(genres)
                album.store()

            return album, genres

        except Exception as e:
            self._log.error(f"Error processing {album.albumartist} - {album.album}: {e}")
            return None

    def _build_direct_url(self, artist: str, album_name: str) -> str:
        """Build direct RYM URL for the given artist and album."""
        # Clean and normalize for URL
        artist_clean = re.sub(r'[^\w\s]', '', artist.lower()).strip()
        artist_clean = re.sub(r'\s+', '-', artist_clean)

        album_clean = re.sub(r'[^\w\s]', '', album_name.lower()).strip()
        album_clean = re.sub(r'\s+', '-', album_clean)

        return f"https://rateyourmusic.com/release/album/{artist_clean}/{album_clean}/"

    def _build_search_url(self, artist: str, album_name: str) -> str:
        """Build RYM search URL for the given artist and album."""
        # Clean up artist and album names
        artist_clean = re.sub(r'[^\w\s]', ' ', artist).strip()
        album_clean = re.sub(r'[^\w\s]', ' ', album_name).strip()

        # Build search query
        query = f"{artist_clean} {album_clean}".strip()
        encoded_query = quote(query)

        return f"https://rateyourmusic.com/search?searchtype=l&searchterm={encoded_query}"

    async def _search_album_url(self, search_url: str, session: aiohttp.ClientSession) -> Optional[str]:
        """Search for album URL on RYM search page."""
        html = await self._fetch_url_with_retry(search_url, session)
        if not html:
            return None

        try:
            soup = BeautifulSoup(html, 'html.parser')

            # Look for album links in search results
            # RYM search results have links with pattern /release/album/...
            album_links = soup.find_all('a', href=re.compile(r'/release/album/'))

            if album_links:
                # Return the first album result
                relative_url = album_links[0]['href']
                return f"https://rateyourmusic.com{relative_url}"

        except Exception as e:
            self._log.error(f"Error parsing search results: {e}")

        return None

    async def _extract_genres_from_url(self, url: str, session: aiohttp.ClientSession) -> List[str]:
        """Extract genre information from an RYM album page."""
        html = await self._fetch_url_with_retry(url, session)
        if not html:
            return []

        try:
            soup = BeautifulSoup(html, 'html.parser')
            genres = []

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

            # Fallback to broader search if no specific structure found
            if not genres:
                genre_links = soup.find_all('a', class_='genre')
                for link in genre_links:
                    genre_text = link.get_text(strip=True)
                    if genre_text and len(genre_text) > 1:
                        genres.append(genre_text)

            # Remove duplicates while preserving order
            seen = set()
            unique_genres = []
            for genre in genres:
                if genre not in seen:
                    seen.add(genre)
                    unique_genres.append(genre)

            return unique_genres

        except Exception as e:
            self._log.error(f"Error extracting genres from {url}: {e}")
            return []

    async def _fetch_url_with_retry(self, url: str, session: aiohttp.ClientSession) -> Optional[str]:
        """Fetch URL using Bright Data API with retry logic."""
        max_retries = self.config['max_retries'].get()
        retry_delay = self.config['retry_delay'].get()

        for attempt in range(max_retries + 1):
            try:
                html = await self._fetch_url_brightdata(url, session)
                if html:
                    return html

            except Exception as e:
                self._log.debug(f"Attempt {attempt + 1} failed for {url}: {e}")

            if attempt < max_retries:
                await asyncio.sleep(retry_delay * (attempt + 1))  # Exponential backoff

        self._log.warning(f"Failed to fetch {url} after {max_retries + 1} attempts")
        return None

    async def _fetch_url_brightdata(self, url: str, session: aiohttp.ClientSession) -> Optional[str]:
        """Fetch URL using Bright Data API."""
        if not self.token:
            return None

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }

        data = {
            "zone": "rym_unlocker",
            "url": url,
            "format": "raw",
            "session": getattr(self, '_bd_session_id', None)  # Reuse session if available
        }

        # Remove None values
        data = {k: v for k, v in data.items() if v is not None}

        try:
            async with session.post(
                "https://api.brightdata.com/request",
                json=data,
                headers=headers
            ) as response:
                if response.status == 200:
                    response_data = await response.json()

                    # Extract and store session ID for reuse
                    if 'session' in response_data:
                        self._bd_session_id = response_data['session']
                        self._log.debug(f"Stored Bright Data session ID: {self._bd_session_id}")

                    # Return the actual content
                    if 'body' in response_data:
                        return response_data['body']
                    else:
                        return response_data.get('content', str(response_data))
                else:
                    self._log.debug(f"Bright Data API returned status {response.status} for {url}")
                    return None

        except Exception as e:
            self._log.debug(f"Bright Data API error for {url}: {e}")
            return None