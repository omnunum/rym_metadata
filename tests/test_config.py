"""Tests for configuration validation and proxy settings."""

import pytest
from unittest.mock import Mock
from rym.config import ProxyConfig


class TestProxyConfig:
    """Test suite for ProxyConfig dataclass."""

    def test_server_url_http(self):
        """Test HTTP server URL generation."""
        config = ProxyConfig(
            enabled=True,
            host="proxy.example.com",
            port=8080,
            use_tls=False
        )

        assert config.server_url == "http://proxy.example.com:8080"

    def test_server_url_https(self):
        """Test HTTPS server URL generation."""
        config = ProxyConfig(
            enabled=True,
            host="proxy.example.com",
            port=8080,
            use_tls=True
        )

        assert config.server_url == "https://proxy.example.com:8080"

    def test_server_url_missing_host(self):
        """Test server URL when host is missing."""
        config = ProxyConfig(
            enabled=True,
            host=None,
            port=8080
        )

        assert config.server_url is None

    def test_server_url_missing_port(self):
        """Test server URL when port is missing."""
        config = ProxyConfig(
            enabled=True,
            host="proxy.example.com",
            port=None
        )

        assert config.server_url is None

    def test_is_valid_complete_config(self):
        """Test validation with complete proxy configuration."""
        config = ProxyConfig(
            enabled=True,
            host="proxy.example.com",
            port=8080,
            username="testuser",
            password="testpass"
        )

        assert config.is_valid is True

    def test_is_valid_disabled_proxy(self):
        """Test validation when proxy is disabled."""
        config = ProxyConfig(enabled=False)
        assert config.is_valid is False

    def test_is_valid_missing_host(self):
        """Test validation when host is missing."""
        config = ProxyConfig(
            enabled=True,
            host=None,
            port=8080,
            username="testuser",
            password="testpass"
        )

        assert config.is_valid is False

    def test_is_valid_missing_port(self):
        """Test validation when port is missing."""
        config = ProxyConfig(
            enabled=True,
            host="proxy.example.com",
            port=None,
            username="testuser",
            password="testpass"
        )

        assert config.is_valid is False

    def test_is_valid_missing_username(self):
        """Test validation when username is missing."""
        config = ProxyConfig(
            enabled=True,
            host="proxy.example.com",
            port=8080,
            username=None,
            password="testpass"
        )

        assert config.is_valid is False

    def test_is_valid_missing_password(self):
        """Test validation when password is missing."""
        config = ProxyConfig(
            enabled=True,
            host="proxy.example.com",
            port=8080,
            username="testuser",
            password=None
        )

        assert config.is_valid is False












