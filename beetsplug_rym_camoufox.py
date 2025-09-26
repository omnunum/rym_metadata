"""RateYourMusic genre scraper plugin for beets using Camoufox.

This plugin scrapes genre information from RateYourMusic using Camoufox
(stealth browser) with Bright Data proxy rotation for CF bypass.
"""

import asyncio
import logging
from typing import List

from beets import plugins, ui
from beets.library import Album
from camoufox import AsyncCamoufox

from rym.session_manager import ProxySessionManager
from rym.cache_manager import HtmlCacheManager
from rym.browser import BrowserManager
from rym.scraper import RYMScraper


class RYMCamoufoxPlugin(plugins.BeetsPlugin):
    """Plugin to fetch genre information from RYM using Camoufox."""

    def __init__(self):
        super().__init__()

        self.config.add({
            # Proxy configuration
            'proxy_enabled': True,
            'proxy_host': None,        # e.g. 'proxy.example.com'
            'proxy_port': None,        # e.g. 8080 (some services use port for IP rotation)
            'proxy_username': None,
            'proxy_password': None,
            'proxy_use_tls': False,    # True = https, False = http
            'proxy_cert_path': None,   # Path to proxy SSL certificate (optional)

            # Proxy rotation method
            'proxy_rotation_method': 'port',  # 'port' or 'username' - how IPs are rotated
            'auto_rotate_on_failure': True,   # Auto-rotate when proxy errors occur

            # Port rotation for IP switching
            'port_range_start': 10001, # Starting port for rotation
            'port_range_end': 10100,   # Ending port for rotation

            # Session management (controls timing/request patterns)
            'session_type': 'const',   # 'sticky', 'rotate', 'const' - when/how sessions change
            'session_duration': 600,   # Session duration in seconds (for sticky)
            'session_id_length': 10,   # Length of generated session IDs

            # Cloudflare challenge settings
            'challenge_wait_time': 60, # Maximum time to wait for challenge solving (increased)
            'challenge_check_interval': 3, # How often to check if challenge is solved

            # Browser and retry settings
            'max_retries': 3,
            'retry_delay': 2.0,
            'page_timeout': 30000,  # 30 seconds

            # Rate limiting
            'min_request_interval': 3.0,  # Minimum seconds between requests (0 = disabled)
            'humanize_request_interval': True,  # Add ±25% random jitter to intervals

            'auto_tag': False,

            # HTML Caching settings
            'cache_enabled': True,     # Enable/disable HTML caching
            'cache_dir': '.rym_cache', # Cache directory path
            'cache_expiry_days': 7,    # HTML cache expiration in days (changed from 0 to avoid constant re-downloading)

            # Genre expansion settings
            'expand_parent_genres': True,  # Automatically add parent genres to album metadata
            'genre_cache_expiry_days': 14,  # Genre hierarchy cache expiration in days (genres rarely change)

            # Resource blocking settings for bandwidth optimization
            'resource_blocking_enabled': True,  # Enable targeted resource blocking (blocks e.snmc.io and asset paths)

            # Search matching settings
            'matching_threshold': 0.8,  # Minimum similarity score (0.0-1.0) for accepting matches
        })

        # Create unified configuration
        from rym.core import RYMConfig
        self.rym_config = RYMConfig.from_beets_config(self.config)

        # Initialize components
        self._init_session_manager()
        self._init_cache_manager()
        self._init_browser_manager()
        self._init_scraper()

        # Validate proxy configuration
        if self.rym_config.proxy_enabled and not self.rym_config.is_proxy_valid:
            self._log.warning("Proxy enabled but missing credentials. Check proxy_host, proxy_port, proxy_username, and proxy_password settings.")

        # Register event listeners for auto-tagging
        if self.config['auto_tag'].get():
            self.register_listener('album_imported', self.album_imported_listener)

    def _init_session_manager(self):
        """Initialize proxy session manager."""
        self.session_manager = None
        if self.rym_config.proxy_enabled and self.rym_config.has_proxy_server:
            self.session_manager = ProxySessionManager(self.rym_config)

    def _init_cache_manager(self):
        """Initialize HTML cache manager."""
        self.cache_manager = None
        if self.rym_config.cache_enabled:
            self.cache_manager = HtmlCacheManager(
                self.rym_config.cache_dir,
                self.rym_config.cache_expiry_days
            )
            # Note: HTML cache cleanup disabled - not currently used and interferes with genre hierarchy cache

    def _init_browser_manager(self):
        """Initialize browser manager."""
        self.browser_manager = BrowserManager(self.rym_config, self.session_manager)

    def _init_scraper(self):
        """Initialize RYM scraper."""
        self.scraper = RYMScraper(
            self.rym_config,
            self.cache_manager,
            self.session_manager,
            self.browser_manager
        )

    def album_imported_listener(self, session, task):
        """Auto-tag albums when they are imported."""
        # Note: session parameter is required by beets but not used
        if task.is_album and hasattr(task, 'album') and task.album:
            album = task.album
            # Only process if we don't already have RYM genres or if force is enabled
            if not album.get('genres'):
                self._log.info(f"Auto-tagging imported album: {album.albumartist} - {album.album}")
                try:
                    # Process the album asynchronously
                    self._process_albums([album], force=False, dry_run=False)
                except Exception as e:
                    self._log.error(f"Error auto-tagging album {album.albumartist} - {album.album}: {e}")

    def commands(self):
        """Register the rym command."""
        cmd = ui.Subcommand('rym', help='fetch genre info from RateYourMusic using Camoufox')
        cmd.parser.add_option('-f', '--force', action='store_true',
                            help='re-fetch genre info even if already present')
        cmd.parser.add_option('-d', '--dry-run', action='store_true',
                            help='show what would be done without making changes')
        cmd.parser.add_option('--debug', action='store_true',
                            help='enable debug logging')
        cmd.parser.add_option('--clear-cache', action='store_true',
                            help='clear all cached HTML data')
        cmd.parser.add_option('--cache-info', action='store_true',
                            help='show cache statistics and exit')
        cmd.func = self.rym_command
        return [cmd]

    def rym_command(self, lib, opts, args):
        """Handle the rym command."""
        if opts.debug:
            self._log.setLevel(logging.DEBUG)
            logging.basicConfig(level=logging.DEBUG)

        # Handle cache management commands
        if opts.cache_info:
            if self.cache_manager:
                cache_info = self.cache_manager.get_cache_info()
                ui.print_(f"Cache directory: {cache_info.get('cache_dir', 'N/A')}")
                ui.print_(f"Total cached files: {cache_info.get('total_files', 0)}")
                ui.print_(f"Total cache size: {cache_info.get('total_size_mb', 0)} MB")
                ui.print_(f"Cache expiry: {cache_info.get('expiry_days', 0)} days {'(never expires)' if cache_info.get('expiry_days', 0) == 0 else ''}")
                if cache_info.get('expired_files', 0) > 0:
                    ui.print_(f"Expired files: {cache_info.get('expired_files', 0)}")
            else:
                ui.print_("Cache is disabled")
            return

        if opts.clear_cache:
            if self.cache_manager:
                cleared_count = self.cache_manager.clear_cache()
                ui.print_(f"Cleared {cleared_count} cache files")
            else:
                ui.print_("Cache is disabled")
            return

        if self.rym_config.proxy_enabled and not self.rym_config.is_proxy_valid:
            ui.print_("Error: No proxy credentials configured")
            return

        query = ui.decargs(args)
        albums = lib.albums(query)

        if not albums:
            ui.print_("No albums found")
            return

        # Run async processing
        asyncio.run(self._process_albums_async(albums, opts.force, opts.dry_run))

    def _process_albums(self, albums: List[Album], force: bool = False, dry_run: bool = False):
        """Sync wrapper for async album processing."""
        asyncio.run(self._process_albums_async(albums, force, dry_run))

    async def _process_albums_async(self, albums: List[Album], force: bool = False, dry_run: bool = False):
        """Process albums using AsyncCamoufox browser with captcha solving."""

        # Filter albums that need processing
        albums_to_process = []
        for album in albums:
            if force or not album.get('genres'):
                albums_to_process.append(album)
            else:
                ui.print_(f"Skipping {album.albumartist} - {album.album} (already has RYM genres)")

        if not albums_to_process:
            ui.print_("No albums need processing")
            return

        # Process albums with async browser
        browser_options = self.browser_manager.get_browser_options()

        try:
            async with AsyncCamoufox(**browser_options) as browser:
                # Create a page
                page = await browser.new_page()

                for i, album in enumerate(albums_to_process, 1):
                    try:
                        result = await self.scraper.process_single_album(album, page, dry_run)
                        if result:
                            album_obj, genre_data = result
                            genres = genre_data.get('genres', [])
                            descriptors = genre_data.get('descriptors', [])

                            output_parts = []
                            if genres:
                                output_parts.append(f"Genres: {', '.join(genres)}")
                            if descriptors:
                                output_parts.append(f"Descriptors: {', '.join(descriptors)}")

                            if output_parts:
                                ui.print_(f"[{i}/{len(albums_to_process)}] {album_obj.albumartist} - {album_obj.album}: {' | '.join(output_parts)}")
                            else:
                                ui.print_(f"[{i}/{len(albums_to_process)}] {album_obj.albumartist} - {album_obj.album}: No genres or descriptors found")
                        else:
                            ui.print_(f"[{i}/{len(albums_to_process)}] Failed to process {album.albumartist} - {album.album}")

                    except Exception as e:
                        ui.print_(f"[{i}/{len(albums_to_process)}] Error processing {album.albumartist} - {album.album}: {e}")
                        self._log.error(f"Error processing album: {e}")

                # Bandwidth optimization statistics are logged automatically during resource blocking

        except Exception as e:
            ui.print_(f"Error initializing browser: {e}")
            self._log.error(f"Browser error: {e}")
            raise