"""Browser management and Cloudflare handling for RYM scraping."""

import asyncio
import logging
import random
import string
import time
from pathlib import Path
from typing import Dict, Optional, Any

from playwright.async_api import Page

from tenacity import retry, stop_after_attempt, wait_exponential
from camoufox_captcha import solve_captcha
from .session_manager import ProxySessionManager
from .dataclasses import RYMConfig


class ServerOverloadError(Exception):
    """Specific exception for 5xx server errors that may benefit from IP rotation."""
    def __init__(self, status_code: int, message: str = None):
        self.status_code = status_code
        super().__init__(message or f"Server error {status_code}")


class BrowserManager:
    """Manages browser configuration, Cloudflare challenges, and resource blocking."""

    def __init__(self, config: RYMConfig, session_manager: Optional[ProxySessionManager] = None) -> None:
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

        # Locks and timestamps for coordinating concurrent page operations
        # Prevents multiple pages from solving the same challenge or rotating IPs simultaneously
        self._challenge_lock = asyncio.Lock()        # Only one page solves challenges at a time
        self._rotation_lock = asyncio.Lock()         # Only one page rotates IP at a time
        self._last_solve_timestamp = None            # When we last successfully solved a challenge
        self._last_rotation_timestamp = None         # When we last successfully rotated IP

    def get_browser_options(self) -> Dict[str, Any]:
        """Get Camoufox browser options with proxy configuration.
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

    def _handle_response_status(self, status_code: int) -> bool:
        """Handle HTTP response status codes.

        Returns:
            True: 200-299, success
            False: 400-499, client error - don't retry
            Raises ServerOverloadError: 500-599, server error - retry via @retry decorator
        """
        if 200 <= status_code < 300:
            return True
        elif 400 <= status_code < 500:
            return False
        elif 500 <= status_code < 600:
            raise ServerOverloadError(status_code, f"Server error {status_code} - retrying")
        else:
            # Unexpected status codes (1xx, 3xx) - treat as success for now
            return True

    async def _handle_server_overload_rotation(self, page: Page, request_timestamp: float) -> bool:
        """Handle IP rotation for server overload errors.

        Uses lock + timestamp pattern to coordinate IP rotation across concurrent pages:
        - Page 1 (T1): Gets 503 → gets lock → rotates → updates last_rotation_timestamp=T5
        - Page 2 (T2): Gets 503 → waits → gets lock → sees T2 < T5 (stale) → just retry
        - Page 2 retry: Uses new IP → success!

        Args:
            page: The page that encountered the error
            request_timestamp: When the request that got the error started

        Returns:
            True if rotation succeeded or error is stale, False if no more IPs available
        """
        if not self.session_manager:
            self.logger.warning("Server overload detected but no session manager available")
            return False

        if not self.config.auto_rotate_on_failure:
            self.logger.warning("Server overload detected but auto_rotate_on_failure is disabled")
            return False

        # Acquire lock to coordinate with other pages
        async with self._rotation_lock:
            # Double-check pattern with timestamp (same logic as challenge solving):
            # If our request started BEFORE the last rotation, we're looking at a stale error
            # from the old IP. Just return True to signal retry - IP is already fresh.

            if self._last_rotation_timestamp and request_timestamp < self._last_rotation_timestamp:
                # Scenario: Page 2 waiting for Page 1 to rotate
                # - Page 1 got 503 at T1, rotated at T5, set last_rotation_timestamp=T5
                # - Page 2 got 503 at T2 (with old IP), waiting for lock
                # - Page 2 gets here: T2 < T5 = True → stale error!
                # - Just return True to retry - IP is already fresh from Page 1's rotation
                self.logger.info("Stale server error (occurred before last rotation), retrying with new IP...")
                return True

            # Fresh error - need to actually rotate IP
            self.logger.warning("Server overload detected, rotating IP")
            self.session_manager.mark_port_blocked()

            if self.session_manager.rotate_port():
                self.logger.info("Rotated to new port, clearing cookies")
                browser_context = page.context
                await browser_context.clear_cookies()

                # Update timestamp only on successful rotation
                self._last_rotation_timestamp = time.time()
                self.logger.info("IP rotated successfully, challenges will be handled automatically on next request")
                return True
            else:
                self.logger.error("No more ports available")
                return False

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=2, min=2, max=30))
    async def navigate_with_protection(self, page: Page, url: str, response_type: str = 'html', **goto_kwargs) -> Optional[Any]:
        """Universal navigation with automatic Cloudflare challenge handling and unified request handling.

        Uses lock + timestamp pattern to coordinate challenge solving across concurrent pages:
        - Page 1 (T1): Hits challenge → gets lock → solves → updates last_solve_timestamp=T5
        - Page 2 (T2): Hits challenge → waits for lock → gets lock → sees T2 < T5 (stale) → raises to retry
        - Page 2 retry: Navigates with fresh cookies → success!

        Only one page actually solves the challenge, others just retry once it's solved.

        Args:
            page: Playwright page instance (caller manages lifecycle)
            url: Target URL to navigate to
            response_type: 'html' for HTML navigation or 'json' for JSON API requests
            **goto_kwargs: Arguments passed to page.goto() (wait_until, timeout, etc.)

        Returns:
            For HTML: HTML string content or None if failed
            For JSON: Parsed JSON object or None if failed
        """
        request_timestamp = time.time()  # Track when this request started

        try:
            # 1. Make the request (branch by request type)
            if response_type == 'json':
                self.logger.debug(f"Making JSON request to {url}")
                response = await page.request.get(url)
            else:
                self.logger.debug(f"Navigating to {url}")
                response = await page.goto(url, **goto_kwargs)

            # 2. Check for Cloudflare challenge
            if await self.is_cloudflare_challenge(page):
                # Acquire lock to coordinate with other pages
                async with self._challenge_lock:
                    # Double-check pattern with timestamp:
                    # - If our request started BEFORE the last solve, we're looking at a stale challenge page
                    # - Just raise to retry - the cookies are already good from the other page's solve
                    # - If our request started AFTER (or no solve yet), this is a fresh challenge we need to solve

                    if self._last_solve_timestamp and request_timestamp < self._last_solve_timestamp:
                        # Scenario: Page 2 waiting for Page 1 to solve
                        # - Page 1 started at T1, solved at T5, set last_solve_timestamp=T5
                        # - Page 2 started at T2 (before T5), sitting on stale challenge
                        # - Page 2 gets here: T2 < T5 = True → stale!
                        # - Just retry navigation - cookies are already fresh
                        self.logger.info("Stale challenge page (requested before last solve), retrying with fresh cookies...")
                        raise Exception("Stale challenge page, retrying with fresh cookies")
                    else:
                        # Scenario: Page 1 - first to encounter challenge, or all pages failed
                        # - No last_solve_timestamp yet, OR
                        # - Our request_timestamp >= last_solve_timestamp (fresh challenge despite previous solve)
                        # - We need to actually solve this challenge
                        self.logger.info("Cloudflare challenge detected")

                        if not await self.solve_cloudflare_challenge(page, url):
                            raise Exception("Cloudflare challenge solving failed")

                        # Update timestamp only on successful solve
                        self._last_solve_timestamp = time.time()
                        self.logger.info("Challenge solved successfully, retrying navigation...")

                        # Raise to trigger @retry with fresh cookies
                        raise Exception("Challenge solved, retrying navigation with fresh cookies")

            # 3. Unified status checking
            if response and hasattr(response, 'status'):
                if not self._handle_response_status(response.status):
                    self.logger.warning(f"Request failed with status {response.status}")
                    return None

            # 4. Return appropriate content (branch by response type)
            if response_type == 'json':
                json_data = await response.json()
                self.logger.debug(f"Successfully parsed JSON response")
                return json_data
            else:
                html_content = await page.content()
                # Update session manager if we have one
                if self.session_manager:
                    self.session_manager.increment_request_count()
                return html_content

        except ServerOverloadError as e:
            # Handle IP rotation for server overload errors before re-raising
            if await self._handle_server_overload_rotation(page, request_timestamp):
                # For JSON requests, restore CF cookies by navigating to homepage
                if response_type == 'json':
                    self.logger.info("JSON request after rotation - navigating to homepage to restore CF cookies")
                    try:
                        await page.goto("https://rateyourmusic.com/", wait_until='domcontentloaded')
                        if await self.is_cloudflare_challenge(page):
                            await self.solve_cloudflare_challenge(page, "https://rateyourmusic.com/")
                    except Exception as cookie_error:
                        self.logger.warning(f"Failed to restore cookies: {cookie_error}")

                self.logger.info("IP rotated, retrying...")
                # Re-raise to let @retry handle the retry
                raise
            else:
                self.logger.error("No more IPs available, giving up")
                return None

        except Exception as e:
            self.logger.error(f"Error during protected navigation to {url}: {e}")
            # Re-raise to let @retry handle it
            raise

    async def is_cloudflare_challenge(self, page: Page) -> bool:
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

    async def solve_cloudflare_challenge(self, page: Page, url: str) -> bool:
        """Solve Cloudflare challenge using camoufox-captcha library."""
        try:
            self.logger.info(f"Attempting to solve Cloudflare challenge for {url}...")

            # Use camoufox-captcha to automatically solve the challenge
            # Use max_retries from config for solve_attempts
            success = await solve_captcha(
                page,
                captcha_type='cloudflare',
                challenge_type='interstitial',
                solve_attempts=self.config.max_retries,
                solve_click_delay=self.config.retry_delay
            )

            if success:
                self.logger.info("Successfully solved Cloudflare challenge!")

                # Extract and save cookies if we have session manager
                if self.session_manager:
                    cookies = await self._extract_cookies(page)
                    if cookies:
                        self.session_manager.set_cookies(cookies)

                return True
            else:
                self.logger.warning("Failed to solve Cloudflare challenge")
                return False

        except Exception as e:
            self.logger.error(f"Error solving Cloudflare challenge: {e}")
            return False

    async def _extract_cookies(self, page: Page) -> Dict[str, str]:
        """Extract cookies from async browser page."""
        try:
            cookies = {}
            cookie_list = await page.context.cookies()
            for cookie in cookie_list:
                cookies[cookie['name']] = cookie['value']

            # Log all cookies first for debugging
            self.logger.debug(f"All available cookies: {list(cookies.keys())}")


            self.logger.debug(f"Extracted {len(cookies)} Cloudflare cookies: {list(cookies.keys())}")
            return cookies
        except Exception as e:
            self.logger.error(f"Error extracting cookies: {e}")
            return {}

    async def apply_session_cookies_to_context(self, browser_context: Any) -> None:
        """Apply saved session cookies to the browser context (all pages inherit automatically)."""
        if not self.session_manager:
            return

        cookies = self.session_manager.get_cookies()
        if not cookies:
            return

        try:
            # Convert dict to cookie format for browser context
            cookie_list = []
            for name, value in cookies.items():
                cookie_list.append({
                    'name': name,
                    'value': value,
                    'domain': 'rateyourmusic.com',
                    'path': '/'
                })

            self.logger.debug(f"Applying {len(cookies)} cookies to browser context: {list(cookies.keys())}")
            await browser_context.add_cookies(cookie_list)
            self.logger.info(f"Successfully applied {len(cookies)} session cookies to browser context")

            # Verify cookies were applied by reading them back
            context_cookies = await browser_context.cookies()
            self.logger.debug(f"Browser context now has {len(context_cookies)} cookies")

        except Exception as e:
            self.logger.error(f"Error applying cookies to browser context: {e}")
            import traceback
            self.logger.error(traceback.format_exc())


    def _build_proxy_username(self) -> str:
        """Build proxy username with session control parameters."""
        base_username = self.config.proxy_username

        # For port-based rotation, always use clean username
        if self.config.proxy_rotation_method == 'port':
            username = base_username
        else:  # username-based rotation
            # Handle session management based on type
            if self.config.session_type == 'const':
                # Use same peer consistently
                username = f"{base_username}-const"
            elif self.config.session_type == 'rotate':
                # Rotate IP for each request (new session every time)
                session_id = ''.join(random.choices(string.ascii_letters + string.digits, k=self.config.session_id_length))
                username = f"{base_username}-session-{session_id}"
            else:  # sticky
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