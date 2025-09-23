"""Proxy session management for RYM scraping."""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, asdict, field


@dataclass
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



class ProxySessionManager:
    """Manages proxy sessions, cookies, and port rotation for efficient scraping."""

    def __init__(self, config: Any, state_file: Optional[str] = None) -> None:
        self.config = config
        # Save state file in current working directory
        self.state_file = Path(state_file or '.rym_session_state.json')
        self.logger = logging.getLogger(__name__)

        # Load existing state or initialize new state
        self.state = self._load_state()

    def _load_state(self) -> SessionState:
        """Load session state from file or create new state."""
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r') as f:
                    state_data = json.load(f)
                    self.logger.debug(f"Loaded session state from {self.state_file}")
                    return SessionState.from_dict(state_data)
            except (json.JSONDecodeError, IOError) as e:
                self.logger.warning(f"Failed to load state file: {e}, creating new state")

        # Create new state
        return SessionState(
            current_port=self.config.port_range_start,
            port_range_min=self.config.port_range_start,
            port_range_max=self.config.port_range_end
        )

    def _save_state(self) -> None:
        """Save current state to file."""
        try:
            with open(self.state_file, 'w') as f:
                json.dump(self.state.to_dict(), f, indent=2, default=str)
            self.logger.debug(f"Saved session state to {self.state_file}")
        except IOError as e:
            self.logger.error(f"Failed to save state file: {e}")

    def get_current_port(self) -> int:
        """Get the current port to use."""
        return self.state.current_port

    def rotate_port(self) -> bool:
        """Rotate to next available port. Returns True if port available, False if exhausted."""
        current_port = self.state.current_port
        blocked_ports = set(self.state.blocked_ports)

        # Find next available port
        for port in range(current_port + 1, self.config.port_range_end + 1):
            if port not in blocked_ports:
                self.state.current_port = port
                self.state.cookies = {}  # Clear cookies for new IP
                self.state.challenge_solved = False
                self.state.session_start_time = None
                self._save_state()
                self.logger.info(f"Rotated to port {port}")
                return True

        self.logger.error("No more ports available in range")
        return False

    def mark_port_blocked(self, port: Optional[int] = None) -> None:
        """Mark a port as blocked."""
        port = port or self.state.current_port
        if port not in self.state.blocked_ports:
            self.state.blocked_ports.append(port)
            self._save_state()
            self.logger.warning(f"Marked port {port} as blocked")

    def set_cookies(self, cookies: Dict[str, str]) -> None:
        """Save cookies from successful challenge solve."""
        self.state.cookies = cookies
        self.state.challenge_solved = True
        self.state.session_start_time = datetime.now().isoformat()
        self.state.last_success_time = datetime.now().isoformat()
        self._save_state()
        self.logger.info(f"Saved {len(cookies)} cookies for session")

    def get_cookies(self) -> Dict[str, str]:
        """Get current session cookies."""
        return self.state.cookies

    def is_session_valid(self) -> bool:
        """Check if current session is still valid."""
        if not self.state.challenge_solved:
            return False

        # Check if cookies exist
        if not self.state.cookies:
            return False

        # Check session age (invalidate after 2 hours)
        if self.state.session_start_time:
            session_start = datetime.fromisoformat(self.state.session_start_time)
            if datetime.now() - session_start > timedelta(hours=2):
                self.logger.info("Session expired due to age")
                return False

        return True

    def increment_request_count(self) -> None:
        """Increment request counter."""
        self.state.request_count += 1
        self.state.last_success_time = datetime.now().isoformat()
        self._save_state()

    def reset_session(self) -> None:
        """Reset current session (e.g., when blocked)."""
        self.state.cookies = {}
        self.state.challenge_solved = False
        self.state.session_start_time = None
        self._save_state()
        self.logger.info("Session reset")