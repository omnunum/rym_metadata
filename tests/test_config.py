"""Tests for configuration validation and proxy settings."""

import pytest
from unittest.mock import Mock
from rym.core import RYMConfig


class TestRYMConfig:
    """Test suite for RYMConfig dataclass."""

    def test_server_url_http(self):
        """Test HTTP server URL generation."""
        config = RYMConfig(
            proxy_enabled=True,
            proxy_host="proxy.example.com",
            proxy_port=8080,
            proxy_use_tls=False
        )

        assert config.proxy_server_url == "http://proxy.example.com:8080"

    def test_server_url_https(self):
        """Test HTTPS server URL generation."""
        config = RYMConfig(
            proxy_enabled=True,
            proxy_host="proxy.example.com",
            proxy_port=8080,
            proxy_use_tls=True
        )

        assert config.proxy_server_url == "https://proxy.example.com:8080"

    def test_server_url_missing_host(self):
        """Test server URL when host is missing."""
        config = RYMConfig(
            proxy_enabled=True,
            proxy_host=None,
            proxy_port=8080
        )

        assert config.proxy_server_url is None

    def test_server_url_missing_port(self):
        """Test server URL when port is missing."""
        config = RYMConfig(
            proxy_enabled=True,
            proxy_host="proxy.example.com",
            proxy_port=None
        )

        assert config.proxy_server_url is None

    def test_is_proxy_valid_complete_config(self):
        """Test validation with complete proxy configuration."""
        config = RYMConfig(
            proxy_enabled=True,
            proxy_host="proxy.example.com",
            proxy_port=8080,
            proxy_username="testuser",
            proxy_password="testpass"
        )

        assert config.is_proxy_valid is True

    def test_is_proxy_valid_disabled_proxy(self):
        """Test validation when proxy is disabled."""
        config = RYMConfig(proxy_enabled=False)
        assert config.is_proxy_valid is False

    def test_is_proxy_valid_missing_host(self):
        """Test validation when host is missing."""
        config = RYMConfig(
            proxy_enabled=True,
            proxy_host=None,
            proxy_port=8080,
            proxy_username="testuser",
            proxy_password="testpass"
        )

        assert config.is_proxy_valid is False

    def test_is_proxy_valid_missing_port(self):
        """Test validation when port is missing."""
        config = RYMConfig(
            proxy_enabled=True,
            proxy_host="proxy.example.com",
            proxy_port=None,
            proxy_username="testuser",
            proxy_password="testpass"
        )

        assert config.is_proxy_valid is False

    def test_is_proxy_valid_missing_username(self):
        """Test validation when username is missing."""
        config = RYMConfig(
            proxy_enabled=True,
            proxy_host="proxy.example.com",
            proxy_port=8080,
            proxy_username=None,
            proxy_password="testpass"
        )

        assert config.is_proxy_valid is False

    def test_is_proxy_valid_missing_password(self):
        """Test validation when password is missing."""
        config = RYMConfig(
            proxy_enabled=True,
            proxy_host="proxy.example.com",
            proxy_port=8080,
            proxy_username="testuser",
            proxy_password=None
        )

        assert config.is_proxy_valid is False












