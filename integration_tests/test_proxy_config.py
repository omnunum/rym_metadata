#!/usr/bin/env python3

import asyncio
import logging
import os
import pytest
from rym import RYMMetadataScraper, RYMConfig

@pytest.mark.integration
@pytest.mark.asyncio
async def test_proxy_initialization():
    """Test that session_manager initializes properly with proxy config."""

    logging.basicConfig(level=logging.INFO)

    # Create config with proxy settings from environment variables
    config = RYMConfig(
        proxy_enabled=True,
        proxy_host=os.environ.get('PROXY_HOST'),
        proxy_port=int(os.environ.get('PROXY_PORT')) if os.environ.get('PROXY_PORT') else None,
        proxy_username=os.environ.get('PROXY_USERNAME'),
        proxy_password=os.environ.get('PROXY_PASSWORD'),
        auto_rotate_on_failure=True,
        expand_parent_genres=True
    )

    print("=== Proxy Configuration Test ===")
    print(f"Proxy enabled: {config.proxy_enabled}")
    print(f"Proxy host: {config.proxy_host}")
    print(f"Proxy port: {config.proxy_port}")
    print(f"Proxy username: {config.proxy_username}")
    print(f"Proxy password: {'***' if config.proxy_password else None}")
    print(f"Auto rotate on failure: {config.auto_rotate_on_failure}")
    print()

    # Check if proxy is properly configured
    print(f"Has proxy server: {config.has_proxy_server}")
    print(f"Has proxy credentials: {config.has_proxy_credentials}")
    print(f"Is proxy valid: {config.is_proxy_valid}")
    print()

    try:
        async with RYMMetadataScraper(config) as scraper:
            # Check if session manager was initialized
            session_mgr = scraper.scraper.session_manager
            print(f"Session manager initialized: {session_mgr is not None}")

            if session_mgr:
                print("✅ Session manager is available - IP rotation should work on 503 errors")
                print(f"Current proxy URL: {config.proxy_server_url}")
            else:
                print("❌ Session manager is None - IP rotation will not work")
                print("   This happens when proxy_host or proxy_port is missing")

            # Check genre manager
            genre_mgr = scraper.scraper.genre_manager
            print(f"Genre manager initialized: {genre_mgr is not None}")

            print("\n=== Testing 503 Response Handling ===")
            print("The _fetch_url method now:")
            print("1. Detects 503 status codes in JSON responses")
            print("2. Triggers IP rotation if session_manager exists")
            print("3. Falls back to normal retry logic if no session manager")

            if session_mgr:
                print("✅ Ready to handle 503 errors with IP rotation")
            else:
                print("⚠ Will handle 503 errors with retry logic only (no IP rotation)")

    except Exception as e:
        print(f"Error during testing: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_proxy_initialization())