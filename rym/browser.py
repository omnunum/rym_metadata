"""Browser management and Cloudflare handling for RYM scraping."""

import logging
import random
import string
import time
from pathlib import Path
from typing import Dict, Optional, Any

from camoufox_captcha import solve_captcha
from .session_manager import ProxySessionManager


class BrowserManager:
    """Manages browser configuration, Cloudflare challenges, and resource blocking."""

    def __init__(self, config: Any, session_manager: Optional[ProxySessionManager] = None) -> None:
        self.config = config
        self.session_manager = session_manager
        self.logger = logging.getLogger(__name__)

        # Session management for sticky sessions
        self.current_session_id = None
        self.session_start_time = None

        # Bandwidth optimization stats
        self.bandwidth_stats = {
            'total_requests': 0,
            'blocked_requests': 0,
            'blocked_types': {}
        }

    def get_browser_options(self, enable_resource_blocking: bool = False) -> Dict[str, Any]:
        """Get Camoufox browser options with proxy configuration.

        Args:
            enable_resource_blocking: Enable resource blocking for bandwidth optimization
        """
        browser_proxy_config = None

        if self.config.is_proxy_valid:
            # Build username with session control (if supported by proxy service)
            username = self._build_proxy_username()

            browser_proxy_config = {
                "server": self.config.proxy_server_url,
                "username": username,
                "password": self.config.proxy_password
            }
            self.logger.debug(f"Using proxy: {self.config.proxy_server_url}")
            self.logger.debug(f"Proxy username: {username}")
            self.logger.debug("Proxy config created successfully")

        # Browser options optimized for Cloudflare captcha solving with camoufox-captcha
        browser_options = {
            'headless': True,  # Run in headless mode
            'humanize': False,  # Disable for captcha solving (recommended by camoufox-captcha)
            'geoip': True if browser_proxy_config else False,  # Enable geoip when using proxy for better stealth
            'disable_coop': True,  # Required for challenge solving
            'i_know_what_im_doing': True,  # Acknowledge COOP disable warning
            'config': {'forceScopeAccess': True},  # Required for closed Shadow DOM traversal
            'window': (1280, 720),  # Proper viewport size for challenges
            'args': ['--ignore-certificate-errors', '--accept-insecure-certs']  # For any HTTPS fallbacks
        }

        # Add proxy if configured
        if browser_proxy_config:
            browser_options['proxy'] = browser_proxy_config

        # Note: Resource blocking is handled via Playwright routes after challenge solving
        # to ensure compatibility with Cloudflare challenge resolution

        # Certificate handling - Camoufox doesn't support ssl_cert parameter
        # For HTTPS proxies with custom certs, this would need to be handled differently
        if self.config.proxy_cert_path and Path(self.config.proxy_cert_path).exists():
            self.logger.debug("SSL certificate found: %s", self.config.proxy_cert_path)
            self.logger.warning("Custom SSL certificates not directly supported by Camoufox - using system cert store")
        elif self.config.proxy_cert_path:
            self.logger.warning("Certificate path specified but file not found: %s", self.config.proxy_cert_path)

        return browser_options

    async def setup_resource_blocking(self, page: Any) -> None:
        """Set up targeted resource blocking using domain/path blocklist."""
        if not self.config.resource_blocking_enabled:
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
                if self.logger.isEnabledFor(logging.DEBUG):
                    self.logger.debug(f"Blocked {resource_type} ({block_reason}): {request_url}")
            else:
                # Allow all other resources
                if self.logger.isEnabledFor(logging.DEBUG):
                    self.logger.debug(f"Allowing {resource_type}: {request_url}")
                await route.continue_()

        # Set up route blocking for all requests
        await page.route("**/*", handle_route)
        blocked_domains_list = ', '.join(sorted(blocked_domains))
        self.logger.info(f"Set up targeted resource blocking. Blocking domains: {blocked_domains_list}")

    def log_bandwidth_stats(self) -> None:
        """Log bandwidth optimization statistics."""
        if self.bandwidth_stats['total_requests'] > 0:
            blocked_pct = (self.bandwidth_stats['blocked_requests'] / self.bandwidth_stats['total_requests']) * 100
            self.logger.info(f"Bandwidth optimization: {self.bandwidth_stats['blocked_requests']}/{self.bandwidth_stats['total_requests']} requests blocked ({blocked_pct:.1f}%)")

            if self.bandwidth_stats['blocked_types']:
                blocked_summary = []
                for resource_type, count in self.bandwidth_stats['blocked_types'].items():
                    blocked_summary.append(f"{resource_type}: {count}")
                self.logger.debug(f"Blocked by type: {', '.join(blocked_summary)}")

    async def solve_cloudflare_challenge(self, page: Any, url: str) -> bool:
        """Solve Cloudflare challenge using camoufox-captcha library."""
        try:
            self.logger.info(f"Attempting to solve Cloudflare challenge for {url}...")

            # Use camoufox-captcha to automatically solve the challenge
            success = await solve_captcha(page, captcha_type='cloudflare', challenge_type='interstitial')

            if success:
                self.logger.info("Successfully solved Cloudflare challenge!")

                # Extract and save cookies if we have session manager
                if self.session_manager:
                    cookies = await self._extract_cookies(page)
                    if cookies:
                        self.session_manager.set_cookies(cookies)

                # Set up resource blocking now that challenge is solved
                await self.setup_resource_blocking(page)

                return True
            else:
                self.logger.warning("Failed to solve Cloudflare challenge")
                return False

        except Exception as e:
            self.logger.error(f"Error solving Cloudflare challenge: {e}")
            return False

    async def _extract_cookies(self, page: Any) -> Dict[str, str]:
        """Extract cookies from async browser page."""
        try:
            cookies = {}
            cookie_list = await page.context.cookies()
            for cookie in cookie_list:
                cookies[cookie['name']] = cookie['value']

            # Filter for Cloudflare-specific cookies
            cf_cookies = {k: v for k, v in cookies.items()
                         if k.startswith(('cf_', '__cf', '__cfduid'))}

            self.logger.debug(f"Extracted {len(cf_cookies)} Cloudflare cookies")
            return cf_cookies
        except Exception as e:
            self.logger.error(f"Error extracting cookies: {e}")
            return {}

    async def apply_session_cookies(self, page: Any) -> None:
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
            self.logger.debug(f"Applied {len(cookies)} session cookies")
        except Exception as e:
            self.logger.error(f"Error applying cookies: {e}")

    def _build_proxy_username(self) -> str:
        """Build proxy username with session control parameters."""
        base_username = self.config.proxy_username

        # Handle session management based on type
        if self.config.session_type == 'none':
            # No session management - use base username
            username = base_username
        elif self.config.session_type == 'const':
            # Use same peer consistently
            username = f"{base_username}-const"
        elif self.config.session_type == 'rotate':
            # Rotate IP for each request (new session every time)
            session_id = ''.join(random.choices(string.ascii_letters + string.digits, k=self.config.session_id_length))
            username = f"{base_username}-session-{session_id}"
        else:  # sticky (default)
            username = self._get_sticky_session_username()

        self.logger.debug(f"Using proxy username: {username}")
        return username

    def _get_sticky_session_username(self) -> str:
        """Get or create sticky session username."""
        current_time = time.time()

        # Check if we need a new session
        if (self.current_session_id is None or
            self.session_start_time is None or
            (current_time - self.session_start_time) > self.config.session_duration):

            # Create new session
            self.current_session_id = ''.join(random.choices(string.ascii_letters + string.digits, k=12))
            self.session_start_time = current_time
            self.logger.info(f"Created new sticky session: {self.current_session_id} (duration: {self.config.session_duration}s)")

        return f"{self.config.proxy_username}-session-{self.current_session_id}"