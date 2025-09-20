"""RateYourMusic genre scraper plugin for beets using Camoufox.

This plugin scrapes genre information from RateYourMusic using Camoufox
(stealth browser) with Bright Data proxy rotation for CF bypass.
"""

import os
import re
import asyncio
import logging
import json
import hashlib
from datetime import datetime, timedelta
from urllib.parse import quote
from typing import List, Optional, Dict, Any, Tuple
from bs4 import BeautifulSoup
from pathlib import Path
from difflib import SequenceMatcher

from beets import plugins, ui
from beets.library import Album
from camoufox import AsyncCamoufox
from camoufox_captcha import solve_captcha


class ProxySessionManager:
    """Manages proxy sessions, cookies, and port rotation for efficient scraping."""

    def __init__(self, proxy_host: str, port_range_start: int = None, port_range_end: int = None,
                 state_file: str = None):
        self.proxy_host = proxy_host
        self.port_range_start = port_range_start or 10001
        self.port_range_end = port_range_end or 10100
        # Save state file in current working directory
        self.state_file = Path(state_file or '.rym_session_state.json')
        self.logger = logging.getLogger(__name__)

        # Load existing state or initialize new state
        self.state = self._load_state()

    def _load_state(self) -> Dict[str, Any]:
        """Load session state from file or create new state."""
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r') as f:
                    state = json.load(f)
                    self.logger.debug(f"Loaded session state from {self.state_file}")
                    return state
            except (json.JSONDecodeError, IOError) as e:
                self.logger.warning(f"Failed to load state file: {e}, creating new state")

        # Create new state
        return {
            'current_port': self.port_range_start,
            'port_range': {'min': self.port_range_start, 'max': self.port_range_end},
            'cookies': {},
            'session_start_time': None,
            'request_count': 0,
            'blocked_ports': [],
            'last_success_time': None,
            'challenge_solved': False
        }

    def _save_state(self):
        """Save current state to file."""
        try:
            with open(self.state_file, 'w') as f:
                json.dump(self.state, f, indent=2, default=str)
            self.logger.debug(f"Saved session state to {self.state_file}")
        except IOError as e:
            self.logger.error(f"Failed to save state file: {e}")

    def get_current_port(self) -> int:
        """Get the current port to use."""
        return self.state['current_port']

    def rotate_port(self) -> bool:
        """Rotate to next available port. Returns True if port available, False if exhausted."""
        current_port = self.state['current_port']
        blocked_ports = set(self.state['blocked_ports'])

        # Find next available port
        for port in range(current_port + 1, self.port_range_end + 1):
            if port not in blocked_ports:
                self.state['current_port'] = port
                self.state['cookies'] = {}  # Clear cookies for new IP
                self.state['challenge_solved'] = False
                self.state['session_start_time'] = None
                self._save_state()
                self.logger.info(f"Rotated to port {port}")
                return True

        self.logger.error("No more ports available in range")
        return False

    def mark_port_blocked(self, port: int = None):
        """Mark a port as blocked."""
        port = port or self.state['current_port']
        if port not in self.state['blocked_ports']:
            self.state['blocked_ports'].append(port)
            self._save_state()
            self.logger.warning(f"Marked port {port} as blocked")

    def set_cookies(self, cookies: Dict[str, str]):
        """Save cookies from successful challenge solve."""
        self.state['cookies'] = cookies
        self.state['challenge_solved'] = True
        self.state['session_start_time'] = datetime.now().isoformat()
        self.state['last_success_time'] = datetime.now().isoformat()
        self._save_state()
        self.logger.info(f"Saved {len(cookies)} cookies for session")

    def get_cookies(self) -> Dict[str, str]:
        """Get current session cookies."""
        return self.state.get('cookies', {})

    def is_session_valid(self) -> bool:
        """Check if current session is still valid."""
        if not self.state.get('challenge_solved', False):
            return False

        # Check if cookies exist
        if not self.state.get('cookies'):
            return False

        # Check session age (invalidate after 2 hours)
        if self.state.get('session_start_time'):
            session_start = datetime.fromisoformat(self.state['session_start_time'])
            if datetime.now() - session_start > timedelta(hours=2):
                self.logger.info("Session expired due to age")
                return False

        return True

    def increment_request_count(self):
        """Increment request counter."""
        self.state['request_count'] = self.state.get('request_count', 0) + 1
        self.state['last_success_time'] = datetime.now().isoformat()
        self._save_state()

    def reset_session(self):
        """Reset current session (e.g., when blocked)."""
        self.state['cookies'] = {}
        self.state['challenge_solved'] = False
        self.state['session_start_time'] = None
        self._save_state()
        self.logger.info("Session reset")


class HtmlCacheManager:
    """Manages HTML caching for RYM pages."""

    def __init__(self, cache_dir: str, expiry_days: int = 0):
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

    def cache_html(self, url: str, html: str):
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

    def clear_cache(self):
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


class RYMCamoufoxPlugin(plugins.BeetsPlugin):
    """Plugin to fetch genre information from RYM using Camoufox."""

    def __init__(self):
        super().__init__()

        self.config.add({
            # Generic proxy configuration
            'proxy_enabled': True,
            'proxy_host': None,        # e.g. 'proxy.example.com'
            'proxy_port': None,        # e.g. 8080 (some services use port for IP rotation)
            'proxy_username': None,
            'proxy_password': None,
            'proxy_type': 'http',      # 'http', 'https', 'socks5'
            'proxy_cert_path': None,   # Path to proxy SSL certificate (optional)

            # Port rotation for IP switching
            'port_range_start': 10001, # Starting port for rotation
            'port_range_end': 10100,   # Ending port for rotation

            # Session management (for services that support it)
            'session_type': 'none',    # 'sticky', 'rotate', 'const', 'none' - default to 'none' for generic proxies
            'session_duration': 600,   # Session duration in seconds (for sticky)
            'session_id_length': 10,   # Length of generated session IDs

            # Cloudflare challenge settings
            'challenge_wait_time': 60, # Maximum time to wait for challenge solving (increased)
            'challenge_check_interval': 3, # How often to check if challenge is solved

            # Browser and retry settings
            'max_retries': 3,
            'retry_delay': 2.0,
            'page_timeout': 30000,  # 30 seconds
            'auto_tag': False,

            # HTML Caching settings
            'cache_enabled': True,     # Enable/disable HTML caching
            'cache_dir': '.rym_cache', # Cache directory path
            'cache_expiry_days': 0,    # Cache expiration in days (0 = never expires)

            # Resource blocking settings for bandwidth optimization
            'resource_blocking_enabled': True,  # Enable targeted resource blocking (blocks e.snmc.io and asset paths)
        })

        # Get proxy configuration
        self.proxy_enabled = self.config['proxy_enabled'].get()

        # Get proxy configuration components
        self.proxy_host = (self.config['proxy_host'].get() or
                          os.environ.get('PROXY_HOST'))
        self.proxy_port = (self.config['proxy_port'].get() or
                          os.environ.get('PROXY_PORT'))
        self.proxy_user = (self.config['proxy_username'].get() or
                          os.environ.get('PROXY_USERNAME'))
        self.proxy_pass = (self.config['proxy_password'].get() or
                          os.environ.get('PROXY_PASSWORD'))
        self.proxy_type = self.config['proxy_type'].get()

        # Build server string from components
        if self.proxy_host and self.proxy_port:
            self.proxy_server = f"{self.proxy_host}:{self.proxy_port}"
        else:
            self.proxy_server = None

        if self.proxy_enabled and not (self.proxy_user and self.proxy_pass and self.proxy_host and self.proxy_port):
            self._log.warning("Proxy enabled but missing credentials. Set PROXY_HOST, PROXY_PORT, PROXY_USERNAME, and PROXY_PASSWORD env vars.")

        # Get certificate path from config or environment
        self.cert_path = (self.config['proxy_cert_path'].get() or
                         os.environ.get('PROXY_CERT_PATH'))

        # Initialize proxy session manager
        self.session_manager = None
        if self.proxy_enabled and self.proxy_host:
            port_start = self.config['port_range_start'].get()
            port_end = self.config['port_range_end'].get()
            self.session_manager = ProxySessionManager(
                proxy_host=self.proxy_host,
                port_range_start=port_start,
                port_range_end=port_end
            )
            # Use session manager's current port instead of config port
            self.proxy_port = self.session_manager.get_current_port()
            if self.proxy_host and self.proxy_port:
                self.proxy_server = f"{self.proxy_host}:{self.proxy_port}"

        # Initialize HTML cache manager
        self.cache_manager = None
        if self.config['cache_enabled'].get():
            cache_dir = self.config['cache_dir'].get()
            cache_expiry = self.config['cache_expiry_days'].get()
            self.cache_manager = HtmlCacheManager(cache_dir, cache_expiry)
            # Clean up expired cache on startup
            if cache_expiry > 0:
                self.cache_manager.cleanup_expired()

        # Initialize bandwidth optimization tracking
        self.bandwidth_stats = {
            'blocked_requests': 0,
            'blocked_types': {},
            'total_requests': 0
        }

        # Session management for rotation control (legacy - will be replaced by session manager)
        self.current_session_id = None
        self.session_start_time = None

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

        if self.proxy_enabled and not (self.proxy_user and self.proxy_pass and self.proxy_server):
            ui.print_("Error: No proxy credentials configured")
            return

        query = ui.decargs(args)
        albums = lib.albums(query)

        if not albums:
            ui.print_("No albums found matching query")
            return

        ui.print_(f"Processing {len(albums)} album(s) with Camoufox...")

        # Run async processing with camoufox-captcha
        import asyncio
        asyncio.run(self._process_albums_async(albums, opts.force, opts.dry_run))

    async def _process_albums_async(self, albums: List[Album], force: bool = False, dry_run: bool = False):
        """Process albums using AsyncCamoufox browser with captcha solving."""

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

        # Process albums with async browser
        browser_options = self._get_browser_options()

        try:
            async with AsyncCamoufox(**browser_options) as browser:
                # Create a page
                page = await browser.new_page()

                for i, album in enumerate(albums_to_process, 1):
                    try:
                        result = await self._process_single_album_async(album, page, dry_run)
                        if result:
                            album_obj, genres = result
                            ui.print_(f"[{i}/{len(albums_to_process)}] {album_obj.albumartist} - {album_obj.album}: {', '.join(genres)}")
                        else:
                            ui.print_(f"[{i}/{len(albums_to_process)}] Failed to process {album.albumartist} - {album.album}")

                    except Exception as e:
                        ui.print_(f"[{i}/{len(albums_to_process)}] Error processing {album.albumartist} - {album.album}: {e}")
                        self._log.error(f"Error processing album: {e}")

                # Log bandwidth optimization statistics
                self._log_bandwidth_stats()

        except Exception as e:
            ui.print_(f"Error creating browser: {e}")
            self._log.error(f"Browser error: {e}")

    def _process_albums(self, albums: List[Album], force: bool = False, dry_run: bool = False):
        """Legacy sync method - deprecated, use _process_albums_async instead."""
        import asyncio
        asyncio.run(self._process_albums_async(albums, force, dry_run))

    def _get_browser_options(self, enable_resource_blocking: bool = False) -> dict:
        """Get Camoufox browser options with proxy configuration.

        Args:
            enable_resource_blocking: Enable resource blocking for bandwidth optimization
        """
        proxy_config = None

        # Update proxy server if using session manager (port may have changed)
        if self.session_manager:
            current_port = self.session_manager.get_current_port()
            if current_port != self.proxy_port:
                self.proxy_port = current_port
                self.proxy_server = f"{self.proxy_host}:{self.proxy_port}"
                self._log.debug(f"Updated proxy port to {current_port}")

        if self.proxy_enabled and self.proxy_user and self.proxy_pass and self.proxy_server:
            # Build username with session control (if supported by proxy service)
            username = self._build_proxy_username()

            # Build proxy server URL with protocol
            proxy_server_url = self._build_proxy_server_url()

            proxy_config = {
                "server": proxy_server_url,
                "username": username,
                "password": self.proxy_pass
            }
            self._log.debug(f"Using proxy: {self.proxy_server}")
            self._log.debug(f"Proxy username: {username}")
            self._log.debug("Proxy config created successfully")

        # Browser options optimized for Cloudflare captcha solving with camoufox-captcha
        browser_options = {
            'headless': True,  # Run in headless mode
            'humanize': False,  # Disable for captcha solving (recommended by camoufox-captcha)
            'geoip': True if proxy_config else False,  # Enable geoip when using proxy for better stealth
            'disable_coop': True,  # Required for challenge solving
            'i_know_what_im_doing': True,  # Acknowledge COOP disable warning
            'config': {'forceScopeAccess': True},  # Required for closed Shadow DOM traversal
            'window': (1280, 720),  # Proper viewport size for challenges
            'args': ['--ignore-certificate-errors', '--accept-insecure-certs']  # For any HTTPS fallbacks
        }

        # Add proxy if configured
        if proxy_config:
            browser_options['proxy'] = proxy_config

        # Note: Resource blocking is handled via Playwright routes after challenge solving
        # to ensure compatibility with Cloudflare challenge resolution

        # Certificate handling - Camoufox doesn't support ssl_cert parameter
        # For HTTPS proxies with custom certs, this would need to be handled differently
        if self.cert_path and Path(self.cert_path).exists():
            self._log.debug("SSL certificate found: %s", self.cert_path)
            self._log.warning("Custom SSL certificates not directly supported by Camoufox - using system cert store")
        elif self.cert_path:
            self._log.warning("Certificate path specified but file not found: %s", self.cert_path)

        return browser_options

    async def _setup_resource_blocking_async(self, page):
        """Set up targeted resource blocking using domain/path blocklist."""
        if not self.config['resource_blocking_enabled'].get():
            return

        # Define blocked domains/paths that are safe to block
        blocked_domains = {
            'e.snmc.io',  # RateYourMusic CDN for images and assets
            'gstatic', # Google static content (fonts, etc.)
            'googletagmanager'
        }

        blocked_paths = {
            '/ads/',
            '/i/',
            '.jpg',
            '.jpeg',
            '.png',
            '.gif',
            '.webp',
            '.svg',
            '.ico',
            '.css',
            '.woff',
            '.woff2',
            '.ttf',
            '.eot'
        }

        async def handle_route(route):
            request_url = route.request.url
            resource_type = route.request.resource_type
            self.bandwidth_stats['total_requests'] += 1

            should_block = False
            block_reason = ""

            # Check if domain is in blocklist
            for domain in blocked_domains:
                if domain in request_url:
                    should_block = True
                    block_reason = f"blocked domain: {domain}"
                    break

            # Check if path contains blocked patterns
            if not should_block:
                for path_pattern in blocked_paths:
                    if path_pattern in request_url.lower():
                        should_block = True
                        block_reason = f"blocked path pattern: {path_pattern}"
                        break

            if should_block:
                await route.abort()
                self.bandwidth_stats['blocked_requests'] += 1
                self.bandwidth_stats['blocked_types'][resource_type] = self.bandwidth_stats['blocked_types'].get(resource_type, 0) + 1
                self._log.debug(f"Blocked {resource_type} ({block_reason}): {request_url}")
            else:
                # Allow all other resources
                self._log.debug(f"Allowing {resource_type}: {request_url}")
                await route.continue_()

        # Set up route blocking for all requests
        await page.route("**/*", handle_route)
        blocked_domains_list = ', '.join(sorted(blocked_domains))
        self._log.info(f"Set up targeted resource blocking. Blocking domains: {blocked_domains_list}")

    def _log_bandwidth_stats(self):
        """Log bandwidth optimization statistics."""
        if self.bandwidth_stats['total_requests'] > 0:
            blocked_pct = (self.bandwidth_stats['blocked_requests'] / self.bandwidth_stats['total_requests']) * 100
            self._log.info(f"Bandwidth optimization: {self.bandwidth_stats['blocked_requests']}/{self.bandwidth_stats['total_requests']} requests blocked ({blocked_pct:.1f}%)")

            if self.bandwidth_stats['blocked_types']:
                blocked_summary = []
                for resource_type, count in self.bandwidth_stats['blocked_types'].items():
                    blocked_summary.append(f"{resource_type}: {count}")
                self._log.debug(f"Blocked by type: {', '.join(blocked_summary)}")

    def _build_proxy_server_url(self) -> str:
        """Build proxy server URL with appropriate protocol."""
        if '://' in self.proxy_server:
            # Server already has protocol
            return self.proxy_server
        else:
            # Add protocol based on proxy_type
            protocol = self.proxy_type
            if protocol == 'socks5':
                return f"socks5://{self.proxy_server}"
            else:
                # Default to http for http/https proxy types
                return f"http://{self.proxy_server}"

    def _build_proxy_username(self) -> str:
        """Build proxy username with session control parameters."""
        import random
        import string

        session_type = self.config['session_type'].get()
        base_username = self.proxy_user

        # Handle session management based on type
        if session_type == 'none':
            # No session management - use base username
            username = base_username
        elif session_type == 'const':
            # Use same peer consistently
            username = f"{base_username}-const"
        elif session_type == 'rotate':
            # Rotate IP for each request (new session every time)
            session_id_length = self.config['session_id_length'].get()
            session_id = ''.join(random.choices(string.ascii_letters + string.digits, k=session_id_length))
            username = f"{base_username}-session-{session_id}"
        else:  # sticky (default)
            username = self._get_sticky_session_username()

        self._log.debug(f"Using proxy username: {username}")
        return username

    def _get_sticky_session_username(self) -> str:
        """Get or create sticky session username."""
        import time
        import random
        import string

        session_duration = self.config['session_duration'].get()
        current_time = time.time()

        # Check if we need a new session
        if (self.current_session_id is None or
            self.session_start_time is None or
            (current_time - self.session_start_time) > session_duration):

            # Create new session
            self.current_session_id = ''.join(random.choices(string.ascii_letters + string.digits, k=12))
            self.session_start_time = current_time
            self._log.info(f"Created new sticky session: {self.current_session_id} (duration: {session_duration}s)")

        return f"{self.proxy_user}-session-{self.current_session_id}"

    async def _process_single_album_async(self, album: Album, page, dry_run: bool = False):
        """Process a single album and extract genre information using async captcha solving."""
        try:
            # Try direct URL first
            direct_url = self._build_direct_url(album.albumartist, album.album)
            self._log.debug("Trying direct URL: %s", direct_url)

            # Test if direct URL works
            genres = await self._extract_genres_from_url_async(direct_url, page)

            # If direct URL fails, fall back to search
            if not genres:
                self._log.debug(f"Direct URL failed, searching for {album.albumartist} - {album.album}")
                search_url = self._build_search_url(album.albumartist, album.album)
                album_year = getattr(album, 'year', None)
                album_url = await self._search_album_url_async(search_url, page, album.albumartist, album.album, album_year)

                if not album_url:
                    self._log.debug(f"No RYM page found for {album.albumartist} - {album.album}")
                    return None

                # Fetch album page and extract genres
                genres = await self._extract_genres_from_url_async(album_url, page)

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

        # Use HTTP since HTTPS has proxy issues
        return f"http://rateyourmusic.com/release/album/{artist_clean}/{album_clean}/"

    def _build_search_url(self, artist: str, album_name: str) -> str:
        """Build RYM search URL for the given artist and album."""
        # Clean up artist and album names
        artist_clean = re.sub(r'[^\w\s]', ' ', artist).strip()
        album_clean = re.sub(r'[^\w\s]', ' ', album_name).strip()

        # Build search query
        query = f"{artist_clean} {album_clean}".strip()
        encoded_query = quote(query)

        return f"http://rateyourmusic.com/search?searchtype=l&searchterm={encoded_query}"

    async def _search_album_url_async(self, search_url: str, page, artist: str, album: str, year: Optional[int] = None) -> Optional[str]:
        """Search for album URL on RYM search page using fuzzy matching."""
        html = await self._fetch_url_with_retry_async(search_url, page)
        if not html:
            return None

        try:
            soup = BeautifulSoup(html, 'html.parser')

            # Get all search results using the infobox structure
            infobox_row = soup.find('tr', class_='infobox')
            if not infobox_row:
                self._log.debug("No infobox row found in search results")
                return None

            # Get all the nested tables within the infobox (each represents a search result)
            result_tables = infobox_row.find_all('table')
            if not result_tables:
                self._log.debug("No result tables found in infobox")
                return None

            self._log.debug(f"Found {len(result_tables)} search result tables")

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
                        self._log.debug(f"Result {i}: {candidate_info['artist']} - {candidate_info['album']} ({candidate_info['year']}) Score: {score:.3f}")
                    else:
                        self._log.debug(f"Result {i}: No album link found in table")

            if not candidates:
                self._log.debug("No valid candidates found")
                return None

            # Sort by score (highest first) and return the best match
            candidates.sort(key=lambda x: x[0], reverse=True)
            best_score, best_info, best_link = candidates[0]

            self._log.info(f"Best match: {best_info['artist']} - {best_info['album']} ({best_info['year']}) Score: {best_score:.3f}")

            relative_url = best_link['href']
            return f"http://rateyourmusic.com{relative_url}"

        except Exception as e:
            self._log.error(f"Error parsing search results: {e}")

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
            self._log.debug(f"Error extracting candidate info: {e}")
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

    async def _extract_genres_from_url_async(self, url: str, page) -> List[str]:
        """Extract genre information from an RYM album page using async."""
        html = await self._fetch_url_with_retry_async(url, page)
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

    async def _solve_cloudflare_challenge_async(self, page, url: str) -> bool:
        """Solve Cloudflare challenge using camoufox-captcha library."""
        try:
            self._log.info(f"Attempting to solve Cloudflare challenge for {url}...")

            # Use camoufox-captcha to automatically solve the challenge
            success = await solve_captcha(page, captcha_type='cloudflare', challenge_type='interstitial')

            if success:
                self._log.info("Successfully solved Cloudflare challenge!")

                # Extract and save cookies if we have session manager
                if self.session_manager:
                    cookies = await self._extract_cookies_async(page)
                    if cookies:
                        self.session_manager.set_cookies(cookies)

                # Set up resource blocking now that challenge is solved
                await self._setup_resource_blocking_async(page)

                return True
            else:
                self._log.warning("Failed to solve Cloudflare challenge")
                return False

        except Exception as e:
            self._log.error(f"Error solving Cloudflare challenge: {e}")
            return False

    async def _extract_cookies_async(self, page) -> Dict[str, str]:
        """Extract cookies from async browser page."""
        try:
            cookies = {}
            cookie_list = await page.context.cookies()
            for cookie in cookie_list:
                cookies[cookie['name']] = cookie['value']

            # Filter for Cloudflare-specific cookies
            cf_cookies = {k: v for k, v in cookies.items()
                         if k.startswith(('cf_', '__cf', '__cfduid'))}

            self._log.debug(f"Extracted {len(cf_cookies)} Cloudflare cookies")
            return cf_cookies
        except Exception as e:
            self._log.error(f"Error extracting cookies: {e}")
            return {}

    async def _apply_session_cookies_async(self, page):
        """Apply saved session cookies to the async page."""
        if not self.session_manager:
            return

        cookies = self.session_manager.get_cookies()
        if not cookies:
            return

        try:
            # Convert dict to cookie format for page
            cookie_list = []
            for name, value in cookies.items():
                cookie_list.append({
                    'name': name,
                    'value': value,
                    'domain': 'rateyourmusic.com',
                    'path': '/'
                })

            await page.context.add_cookies(cookie_list)
            self._log.debug(f"Applied {len(cookies)} session cookies")
        except Exception as e:
            self._log.error(f"Error applying cookies: {e}")


    async def _fetch_url_with_retry_async(self, url: str, page) -> Optional[str]:
        """Fetch URL using AsyncCamoufox with automatic captcha solving and session management."""
        # Check cache first
        if self.cache_manager:
            cached_html = self.cache_manager.get_cached_html(url)
            if cached_html:
                return cached_html

        max_retries = self.config['max_retries'].get()

        for attempt in range(max_retries + 1):
            try:
                self._log.debug(f"Fetching URL (attempt {attempt + 1}): {url}")

                # Check if we have a valid session and apply cookies
                if self.session_manager and self.session_manager.is_session_valid():
                    await self._apply_session_cookies_async(page)
                    self._log.debug("Using existing session cookies")
                    # Set up resource blocking since we already have a valid session
                    await self._setup_resource_blocking_async(page)

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
                    challenge_solved = await self._solve_cloudflare_challenge_async(page, url)
                    if challenge_solved:
                        # Wait a bit more after challenge is solved
                        await page.wait_for_load_state('networkidle', timeout=5000)
                except Exception as e:
                    # If challenge solving fails, we might still have gotten through
                    self._log.debug(f"Challenge solving attempt failed: {e}")

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
                    self._log.debug(f"Got minimal content, may be blocked: {len(html) if html else 0} chars")

            except Exception as e:
                error_msg = str(e)
                self._log.debug(f"Attempt {attempt + 1} failed for {url}: {error_msg}")

                # Check for specific proxy errors that indicate port should be rotated
                if any(error in error_msg for error in ["PROXY_FORBIDDEN", "403", "PROXY_CONNECTION_FAILED", "CONNECTION_REFUSED"]):
                    if self.session_manager:
                        self._log.warning("Proxy error detected, marking port as blocked")
                        self.session_manager.mark_port_blocked()
                        if self.session_manager.rotate_port():
                            self._log.info("Rotated to new port, will retry")
                            # Update proxy configuration for next attempt
                            self.proxy_port = self.session_manager.get_current_port()
                            self.proxy_server = f"{self.proxy_host}:{self.proxy_port}"
                            continue
                        else:
                            self._log.error("No more ports available")
                            return None

                # Check for other errors
                if "CERTIFICATE" in error_msg.upper():
                    self._log.warning("SSL certificate issue - may need custom certificate configuration")

            if attempt < max_retries:
                retry_delay = self.config['retry_delay'].get()
                await asyncio.sleep(retry_delay * (attempt + 1))  # Exponential backoff

        self._log.warning(f"Failed to fetch {url} after {max_retries + 1} attempts")
        return None

