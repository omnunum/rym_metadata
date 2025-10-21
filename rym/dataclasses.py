from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Literal, Optional

@dataclass(repr=True)
class RYMConfig:
    """Configuration for standalone RYM scraper."""
    # Base URL configuration
    base_url: str = "https://rateyourmusic.com"  # RYM base URL (configurable for testing/mirrors)

    # Proxy configuration
    proxy_enabled: bool = False  # Disabled by default for simplicity
    proxy_host: Optional[str] = None
    proxy_port: Optional[int] = None
    proxy_username: Optional[str] = None
    proxy_password: Optional[str] = None
    proxy_use_tls: bool = False
    proxy_cert_path: Optional[str] = None

    # Proxy rotation method
    proxy_rotation_method: Literal['port', 'username'] = 'port'  # How IPs are rotated
    auto_rotate_on_failure: bool = True  # Auto-rotate when proxy errors occur

    # Session management (controls timing/request patterns)
    session_type: Literal['sticky', 'rotate', 'const'] = 'const'  # When/how sessions change
    session_duration: int = 600
    session_id_length: int = 10
    port_range_start: int = 10001
    port_range_end: int = 10100

    # Browser and retry settings
    max_retries: int = 5
    retry_delay: float = 2.0
    page_timeout: int = 30000
    headless: bool = True  # Run browser in headless mode (set to False for debugging captchas)

    # Rate limiting
    min_request_interval: float = 3.0  # Minimum seconds between requests (0 = disabled)
    humanize_request_interval: bool = True  # Add ±25% random jitter to intervals

    # Cache settings
    cache_enabled: bool = True
    cache_dir: str = '.rym_cache'
    cache_expiry_days: int = 7  # Cache for a week by default

    # Session state file path
    session_state_file_path: Optional[str] = None  # Defaults to .rym_session_state.json in current directory

    # Resource blocking
    resource_blocking_enabled: bool = True

    # Search matching
    matching_threshold: float = 0.8  # Minimum similarity score (0.0-1.0) for accepting matches

    # Genre expansion
    expand_parent_genres: bool = True  # Automatically add parent genres to album metadata
    genre_cache_expiry_days: int = 30  # How long to cache genre hierarchy data (0 = never expire)

    # Direct file tagging (for beets plugin)
    write_tags_to_files: bool = False  # Write genres/descriptors directly to audio files using mutagen

    @classmethod
    def from_beets_config(cls, config) -> 'RYMConfig':
        """Create RYMConfig from beets configuration object."""
        return cls(
            # Base URL configuration
            base_url=config['base_url'].get("https://rateyourmusic.com"),

            # Proxy configuration
            proxy_enabled=config['proxy_enabled'].get(),
            proxy_host=config['proxy_host'].get(),
            proxy_port=config['proxy_port'].get(),
            proxy_username=config['proxy_username'].get(),
            proxy_password=config['proxy_password'].get(),
            proxy_use_tls=config['proxy_use_tls'].get(False),
            proxy_cert_path=config['proxy_cert_path'].get(),

            # Proxy rotation method
            proxy_rotation_method=config['proxy_rotation_method'].get('port'),
            auto_rotate_on_failure=config['auto_rotate_on_failure'].get(True),

            # Session management
            session_type=config['session_type'].get('const'),
            session_duration=config['session_duration'].get(600),
            session_id_length=config['session_id_length'].get(10),
            port_range_start=config['port_range_start'].get(10001),
            port_range_end=config['port_range_end'].get(10100),

            # Browser and retry settings
            max_retries=config['max_retries'].get(3),
            retry_delay=config['retry_delay'].get(2.0),
            page_timeout=config['page_timeout'].get(30000),
            headless=config['headless'].get(True),

            # Rate limiting
            min_request_interval=config['min_request_interval'].get(3.0),
            humanize_request_interval=config['humanize_request_interval'].get(True),

            # Cache settings
            cache_enabled=config['cache_enabled'].get(True),
            cache_dir=config['cache_dir'].get('.rym_cache'),
            cache_expiry_days=config['cache_expiry_days'].get(0),

            # Session state file path
            session_state_file_path=config['session_state_file_path'].get(),

            # Resource blocking
            resource_blocking_enabled=config['resource_blocking_enabled'].get(True),

            # Search matching
            matching_threshold=config['matching_threshold'].get(0.8),

            # Genre expansion
            expand_parent_genres=config['expand_parent_genres'].get(True),
            genre_cache_expiry_days=config['genre_cache_expiry_days'].get(30),

            # Direct file tagging
            write_tags_to_files=config['write_tags_to_files'].get(False),
        )

    @property
    def proxy_server_url(self) -> Optional[str]:
        """Build complete proxy server URL with protocol."""
        if not (self.proxy_host and self.proxy_port):
            return None
        protocol = "https" if self.proxy_use_tls else "http"
        return f"{protocol}://{self.proxy_host}:{self.proxy_port}"

    @property
    def is_proxy_valid(self) -> bool:
        """Check if proxy configuration is complete."""
        return (self.proxy_enabled and
                self.proxy_host is not None and
                self.proxy_port is not None and
                self.proxy_username is not None and
                self.proxy_password is not None)

    @property
    def has_proxy_credentials(self) -> bool:
        """Check if proxy username and password are provided."""
        return self.proxy_username is not None and self.proxy_password is not None

    @property
    def has_proxy_server(self) -> bool:
        """Check if proxy host and port are provided."""
        return self.proxy_host is not None and self.proxy_port is not None


@dataclass(repr=True)
class RYMMetadata:
    """Container for RYM metadata (artist or album)."""
    artist: str
    genres: List[str]
    descriptors: List[str]
    url: Optional[str] = None
    album: Optional[str] = None  # None for artist-only metadata
    album_type: Optional[str] = None  # "album", "single", "ep", "compilation"


@dataclass(repr=True)
class DiscographyCandidate:
    """Container for discography search candidate."""
    album: str
    year: Optional[int]
    url: str


@dataclass(repr=True)
class SessionState:
    """Session state for proxy management and cookies."""
    current_port: int
    port_range_min: int
    port_range_max: int
    cookies: Dict[str, str] = field(default_factory=dict)
    session_start_time: Optional[str] = None
    request_count: int = 0
    blocked_ports: List[int] = field(default_factory=list)
    last_success_time: Optional[str] = None
    challenge_solved: bool = False

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SessionState':
        """Create SessionState from dictionary (for loading from JSON)."""
        # Handle legacy port_range format
        if 'port_range' in data and isinstance(data['port_range'], dict):
            port_range = data['port_range']
            data['port_range_min'] = port_range.get('min', data.get('current_port', 10001))
            data['port_range_max'] = port_range.get('max', data.get('current_port', 10100))
            del data['port_range']

        # Only include fields that exist in the dataclass
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered_data = {k: v for k, v in data.items() if k in valid_fields}

        return cls(**filtered_data)

    def to_dict(self) -> Dict[str, Any]:
        """Convert SessionState to dictionary (for saving to JSON)."""
        data = asdict(self)
        # Keep legacy port_range format for backward compatibility
        data['port_range'] = {'min': self.port_range_min, 'max': self.port_range_max}
        return data