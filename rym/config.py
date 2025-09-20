"""Configuration classes for RYM scraping components."""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ProxyConfig:
    """Proxy configuration with type safety and validation."""

    # Core proxy settings
    enabled: bool
    host: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None
    use_tls: bool = False  # True = https/ssl, False = http
    cert_path: Optional[str] = None

    # Session management
    session_type: str = 'none'  # 'sticky', 'rotate', 'const', 'none'
    session_duration: int = 600
    session_id_length: int = 10

    # Port rotation
    port_range_start: int = 10001
    port_range_end: int = 10100

    @classmethod
    def from_beets_config(cls, config) -> 'ProxyConfig':
        """Create ProxyConfig from beets configuration object."""
        return cls(
            enabled=config['proxy_enabled'].get(),
            host=config['proxy_host'].get(),
            port=config['proxy_port'].get(),
            username=config['proxy_username'].get(),
            password=config['proxy_password'].get(),
            use_tls=config['proxy_use_tls'].get(False),
            cert_path=config['proxy_cert_path'].get(),
            session_type=config['session_type'].get(),
            session_duration=config['session_duration'].get(),
            session_id_length=config['session_id_length'].get(),
            port_range_start=config['port_range_start'].get(),
            port_range_end=config['port_range_end'].get(),
        )

    @property
    def server_url(self) -> Optional[str]:
        """Build complete proxy server URL with protocol."""
        if not (self.host and self.port):
            return None
        protocol = "https" if self.use_tls else "http"
        return f"{protocol}://{self.host}:{self.port}"

    @property
    def is_valid(self) -> bool:
        """Check if configuration is complete for proxy use."""
        return (self.enabled and
                self.host is not None and
                self.port is not None and
                self.username is not None and
                self.password is not None)

    @property
    def has_credentials(self) -> bool:
        """Check if username and password are provided."""
        return self.username is not None and self.password is not None

    @property
    def has_server(self) -> bool:
        """Check if host and port are provided."""
        return self.host is not None and self.port is not None