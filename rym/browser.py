"""Browser management and Cloudflare handling for RYM scraping."""

import asyncio
import logging
import random
import string
import time
from pathlib import Path
from typing import Dict, Optional, Any

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

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

            # Build proxy URL with current port (for port-based rotation)
            if self.config.proxy_rotation_method == 'port' and self.session_manager:
                current_port = self.session_manager.get_current_port()
                protocol = "https" if self.config.proxy_use_tls else "http"
                proxy_url = f"{protocol}://{self.config.proxy_host}:{current_port}"
            else:
                proxy_url = self.config.proxy_server_url

            browser_proxy_config = {
                "server": proxy_url,
                "username": username,
                "password": self.config.proxy_password
            }
            self.logger.debug(f"Using proxy: {proxy_url}")
            self.logger.debug(f"Proxy username: {username}")
            self.logger.debug("Proxy config created successfully")

        # Browser options optimized for Cloudflare captcha solving with camoufox-captcha
        browser_options = {
            'headless': self.config.headless,  # Configurable: set to False in config for debugging
            'humanize': False,  # Required: Disable for captcha solving (recommended by camoufox-captcha)
            'geoip': True,  # Required: Enable for realistic fingerprinting (especially with proxy)
            'disable_coop': True,  # Required: Essential for security bypass and Shadow DOM traversal
            'i_know_what_im_doing': True,  # Required: Acknowledge COOP disable warning
            'config': {'forceScopeAccess': True},  # Required: Essential for closed Shadow DOM traversal
            'window': (1280, 720),  # Proper viewport size for consistent challenge rendering
            'args': ['--ignore-certificate-errors', '--accept-insecure-certs']  # For any HTTPS fallbacks
        }

        if not self.config.headless:
            self.logger.info("Running in non-headless mode for debugging")

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
        # Note: Allowing googletagmanager and other tracking scripts to ensure
        # session cookies like _pubcid are properly set
        blocked_domains = {
            'e.snmc.io',  # RateYourMusic CDN for images and assets
            'gstatic'  # Google static content (fonts, etc.)
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

            # Check if domain is in blocklist
            for domain in blocked_domains:
                if domain in request_url:
                    should_block = True
                    break

            # Check if path contains blocked patterns
            if not should_block:
                for path_pattern in blocked_paths:
                    if path_pattern in request_url.lower():
                        should_block = True
                        break

            if should_block:
                await route.abort()
                self.bandwidth_stats['blocked_requests'] += 1
                self.bandwidth_stats['blocked_types'][resource_type] = self.bandwidth_stats['blocked_types'].get(resource_type, 0) + 1
            else:
                # CRITICAL FIX: Firefox 133+ iframe caching bug workaround
                # Only apply fetch/fulfill pattern to iframe subdocuments (where Turnstile loads)
                # Applying to all requests breaks response bodies for main documents
                # See: https://github.com/daijro/camoufox/issues/150
                if resource_type == 'iframe' or resource_type == 'subdocument':
                    try:
                        response = await route.fetch()
                        await route.fulfill(body=await response.body())
                    except Exception as e:
                        # Fallback to normal continue if fetch/fulfill fails
                        if self.logger.isEnabledFor(logging.DEBUG):
                            self.logger.debug(f"Route fetch/fulfill failed for iframe {request_url}, using continue: {e}")
                        await route.continue_()
                else:
                    # For all other resource types, use normal routing
                    await route.continue_()

        # Set up route blocking for all requests
        await page.route("**/*", handle_route)
        blocked_domains_list = ', '.join(sorted(blocked_domains))
        self.logger.info(f"Set up targeted resource blocking. Blocking domains: {blocked_domains_list}")

    def _handle_response_status(self, status_code: int) -> bool:
        """Handle HTTP response status codes.

        Returns:
            True: 200-299, success
            False: 400-499, client error - don't retry (except 403)
            Raises ServerOverloadError: 403, 500-599 - triggers IP rotation
        """
        if 200 <= status_code < 300:
            return True
        elif status_code == 403:
            # 403 from Cloudflare typically means IP is blocked - rotate
            raise ServerOverloadError(status_code, f"IP blocked (403) - rotating")
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

    def _is_challenge(self, response, content: str) -> bool:
        """Simple challenge detection using header and content."""
        # Method 1: Check cf-mitigated header
        if hasattr(response, 'headers') and response.headers.get('cf-mitigated') == 'challenge':
            return True

        # Method 2: Check for challenge HTML in content
        if 'Just a moment...' in content and '<title>Just a moment...</title>' in content:
            return True

        return False

    async def _solve_challenge_on_homepage(self, page: Page):
        """Navigate to homepage and solve challenge if present."""
        self.logger.info("Navigating to homepage to solve challenge...")
        response = await page.goto("https://rateyourmusic.com/", wait_until='domcontentloaded')
        html = await page.content()

        if self._is_challenge(response, html):
            self.logger.info("Challenge detected on homepage, solving...")
            await page.wait_for_load_state('networkidle', timeout=30000)
            if not await self.solve_cloudflare_challenge(page, "https://rateyourmusic.com/"):
                raise Exception("Challenge solving failed on homepage")
            # Wait after solving to avoid rate limits
            self.logger.info("Challenge solved, waiting 5s to avoid rate limits...")
            await asyncio.sleep(5)

        else:
            self.logger.info("No challenge on homepage")

    async def _solve_challenge_on_current_page(self, page: Page, url: str):
        """Solve challenge on the current page."""
        await page.wait_for_load_state('networkidle', timeout=30000)

        async with self._challenge_lock:
            if not await self.solve_cloudflare_challenge(page, url):
                raise Exception("Challenge solving failed")

            self._last_solve_timestamp = time.time()
            # Wait after solving to avoid rate limits
            self.logger.info("Challenge solved, waiting 5s to avoid rate limits...")
            await asyncio.sleep(5)

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=2, min=2, max=30))
    async def navigate_with_protection(self, page: Page, url: str, response_type: str = 'html', method: str = 'GET', form_data: Optional[Dict[str, str]] = None) -> Optional[Any]:
        """Simplified navigation with automatic Cloudflare challenge handling.

        Args:
            page: Playwright page instance (caller manages lifecycle)
            url: Target URL to navigate to
            response_type: 'html' for HTML navigation or 'json' for JSON API requests
            method: HTTP method ('GET' or 'POST')
            form_data: Form data for POST requests

        Returns:
            For HTML: HTML string content or None if failed
            For JSON: Parsed JSON object or None if failed
        """
        try:
            # 1. Make the request
            if response_type == 'json':
                self.logger.debug(f"Making {method} request to {url}")

                # Use page.evaluate() with fetch() to make the request from browser context
                # This ensures all cookies, headers, and browser fingerprints are included
                if method == 'POST' and form_data:
                    fetch_result = await page.evaluate("""
                        async (data) => {
                            const formData = new FormData();
                            for (const [key, value] of Object.entries(data.formData)) {
                                formData.append(key, value);
                            }
                            const response = await fetch(data.url, {
                                method: 'POST',
                                headers: {
                                    'X-Requested-With': 'XMLHttpRequest'
                                },
                                body: formData,
                                credentials: 'include'
                            });

                            const text = await response.text();
                            const headers = {};
                            response.headers.forEach((value, key) => {
                                headers[key] = value;
                            });

                            return {
                                status: response.status,
                                statusText: response.statusText,
                                headers: headers,
                                text: text
                            };
                        }
                    """, {'url': url, 'formData': form_data})
                else:
                    fetch_result = await page.evaluate("""
                        async (url) => {
                            const response = await fetch(url, {
                                method: 'GET',
                                headers: {
                                    'accept': '*/*',
                                    'referer': 'https://rateyourmusic.com/genres/'
                                },
                                credentials: 'include'
                            });

                            const text = await response.text();
                            const headers = {};
                            response.headers.forEach((value, key) => {
                                headers[key] = value;
                            });

                            return {
                                status: response.status,
                                statusText: response.statusText,
                                headers: headers,
                                text: text
                            };
                        }
                    """, url)

                # Create a mock response object that matches what we expect
                class MockResponse:
                    def __init__(self, data):
                        self.status = data['status']
                        self.statusText = data['statusText']
                        self.headers = data['headers']
                        self._text = data['text']

                    async def text(self):
                        return self._text

                    async def json(self):
                        import json
                        return json.loads(self._text)

                response = MockResponse(fetch_result)
            else:
                self.logger.debug(f"Navigating to {url}")
                response = await page.goto(url, wait_until='commit', timeout=10000)

            # 2. Get content and check for challenge
            content = await response.text() if response_type == 'json' else await page.content()

            if self._is_challenge(response, content):
                self.logger.info(f"Challenge detected for {url}")

                # Cloudflare can challenge API requests separately from HTML pages
                # We need to navigate to the actual URL that's challenged, not the homepage
                # This will show the Turnstile challenge widget which we can solve
                if response_type == 'json':
                    # For JSON endpoints, solve challenge on homepage (can't solve on JSON response)
                    await self._solve_challenge_on_homepage(page)
                else:
                    # For HTML, solve on current page
                    await self._solve_challenge_on_current_page(page, url)

                # After solving, retry this request
                raise Exception("Challenge solved, retrying request")

            # 3. Check status code
            if response.status != 200:
                if response.status == 503:
                    raise ServerOverloadError(f"503 error from {url}")
                elif response.status >= 400:
                    self.logger.warning(f"Request failed with status {response.status}")
                    return None

            # 4. Wait for DOM load if needed (HTML only)
            if response_type == 'html':
                await page.wait_for_load_state('domcontentloaded', timeout=15000)

            # 5. Return content
            if response_type == 'json':
                # For POST requests, return raw text (JavaScript callback format)
                # For GET requests, parse as JSON
                if method == 'POST':
                    return content
                else:
                    return await response.json()
            else:
                html_content = await page.content()
                if self.session_manager:
                    self.session_manager.increment_request_count()
                self.logger.debug(f"Received HTML content: {len(html_content)} bytes")
                return html_content

        except ServerOverloadError:
            # Handle IP rotation
            request_timestamp = time.time()
            if await self._handle_server_overload_rotation(page, request_timestamp):
                self.logger.info("IP rotated, retrying request")
                raise  # Let @retry handle the retry
            else:
                self.logger.error("No more IPs available")
                return None

        except Exception as e:
            self.logger.error(f"Error during navigation to {url}: {e}")
            raise

    async def solve_cloudflare_challenge(self, page: Page, url: str) -> bool:
        """Solve Cloudflare challenge using camoufox-captcha library with optimal settings."""
        try:
            self.logger.info(f"Attempting to solve Cloudflare challenge for {url}...")

            # Use camoufox-captcha with optimal settings for Cloudflare
            # Optimized parameters based on camoufox-captcha best practices:
            # - CRITICAL: solve_click_delay must be 8-10s to allow Cloudflare verification to complete
            # - Longer delays = more human-like behavior and allow backend verification
            # - More wait attempts = better for slow-loading challenges
            success = await solve_captcha(
                page,
                captcha_type='cloudflare',
                challenge_type='interstitial',
                method='click',  # Explicit method for clarity
                solve_attempts=max(self.config.max_retries, 5),  # At least 5 attempts
                solve_click_delay=10.0,  # CRITICAL: 10s wait after click for Cloudflare verification
                wait_checkbox_attempts=10,  # Increased from default 5 for slow challenges
                wait_checkbox_delay=3.0,  # Increased from default 1s for better reliability
                checkbox_click_attempts=3,  # Default is good
                attempt_delay=5  # Delay between solve attempts
            )

            if success:
                self.logger.info("Successfully solved Cloudflare challenge!")

                # CRITICAL: Additional wait after solve to ensure cookies are fully set
                # Cloudflare may still be processing verification in background
                self.logger.debug("Waiting additional 3 seconds for cookie propagation...")
                await asyncio.sleep(3)

                # Extract and save cookies if we have session manager
                if self.session_manager:
                    cookies = await self._extract_cookies(page)
                    if cookies:
                        self.session_manager.set_cookies(cookies)
                        self.logger.debug(f"Saved {len(cookies)} cookies from successful challenge solve")

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

            # Log all cookies for debugging
            self.logger.debug(f"Extracted {len(cookies)} total cookies: {list(cookies.keys())}")
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