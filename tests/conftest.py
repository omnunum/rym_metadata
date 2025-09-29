"""Pytest configuration and fixtures for RYM tests."""

import json
import pytest
from pathlib import Path
from unittest.mock import Mock
from rym.dataclasses import RYMConfig
from rym.content_cache_manager import ContentCacheManager


@pytest.fixture
def fixtures_dir():
    """Return path to test fixtures directory."""
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def cache_fixtures_dir(fixtures_dir):
    """Return path to cache fixtures directory."""
    return fixtures_dir / "cache"


@pytest.fixture
def sample_cache_data(cache_fixtures_dir):
    """Load sample cache data from fixtures."""
    cache_files = list(cache_fixtures_dir.glob("*.json"))
    if not cache_files:
        pytest.skip("No cache fixtures available")

    # Load first cache file as sample
    with open(cache_files[0], 'r', encoding='utf-8') as f:
        return json.load(f)


@pytest.fixture
def temp_cache_dir(tmp_path):
    """Create temporary cache directory for tests."""
    return tmp_path / "test_cache"


@pytest.fixture
def mock_rym_config():
    """Create mock RYM configuration for testing."""
    return RYMConfig(
        proxy_enabled=True,
        proxy_host="proxy.example.com",
        proxy_port=8080,
        proxy_username="testuser",
        proxy_password="testpass",
        proxy_use_tls=False,
        proxy_rotation_method='port',
        auto_rotate_on_failure=True,
        session_type="const",
        session_duration=600,
        session_id_length=10,
        port_range_start=10001,
        port_range_end=10100,
        # Disable rate limiting for tests to avoid timing issues
        min_request_interval=0.0,
        humanize_request_interval=False
    )




@pytest.fixture
def cache_manager(temp_cache_dir):
    """Create cache manager instance for testing."""
    return ContentCacheManager(str(temp_cache_dir))


@pytest.fixture
def sample_search_html():
    """Sample RYM search results HTML."""
    return '''
    <html>
    <body>
        <tr class="infobox">
            <td>
                <table>
                    <tr>
                        <td><a class="artist" href="/artist/kollektiv-turmstrasse">Kollektiv Turmstrasse</a></td>
                        <td><a class="searchpage" href="/release/album/kollektiv-turmstrasse/musik-gewinnt-freunde-collection/">Musik Gewinnt Freunde Collection</a></td>
                        <td>2013</td>
                    </tr>
                </table>
                <table>
                    <tr>
                        <td><a class="artist" href="/artist/other-artist">Other Artist</a></td>
                        <td><a class="searchpage" href="/release/album/other-artist/different-album/">Different Album</a></td>
                        <td>2012</td>
                    </tr>
                </table>
            </td>
        </tr>
    </body>
    </html>
    '''


@pytest.fixture
def sample_album_html():
    """Sample RYM album page HTML with genres."""
    return '''
    <html>
    <body>
        <tr class="release_genres">
            <td>
                <span class="release_pri_genres">
                    <a class="genre" href="/genre/electronic">Electronic</a>
                    <a class="genre" href="/genre/house">House</a>
                </span>
            </td>
        </tr>
        <tr class="release_descriptors">
            <td>
                <meta content="Deep House" />
                <meta content="Minimal Techno" />
            </td>
        </tr>
    </body>
    </html>
    '''