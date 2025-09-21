"""Proxy session management for RYM scraping."""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, Optional



class ProxySessionManager:
    """Manages proxy sessions, cookies, and port rotation for efficient scraping."""

    def __init__(self, config: Any, state_file: Optional[str] = None) -> None:
        self.config = config
        # Save state file in current working directory
        self.state_file = Path(state_file or '.rym_session_state.json')
        self.logger = logging.getLogger(__name__)

        # Load existing state or initialize new state
        self.state = self._load_state()

    def _load_state(self) -> Dict[str, Any]:
        """Load session state from file or create new state."""
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r') as f:
                    state = json.load(f)
                    self.logger.debug(f"Loaded session state from {self.state_file}")
                    return state
            except (json.JSONDecodeError, IOError) as e:
                self.logger.warning(f"Failed to load state file: {e}, creating new state")

        # Create new state
        return {
            'current_port': self.config.port_range_start,
            'port_range': {'min': self.config.port_range_start, 'max': self.config.port_range_end},
            'cookies': {},
            'session_start_time': None,
            'request_count': 0,
            'blocked_ports': [],
            'last_success_time': None,
            'challenge_solved': False
        }

    def _save_state(self) -> None:
        """Save current state to file."""
        try:
            with open(self.state_file, 'w') as f:
                json.dump(self.state, f, indent=2, default=str)
            self.logger.debug(f"Saved session state to {self.state_file}")
        except IOError as e:
            self.logger.error(f"Failed to save state file: {e}")

    def get_current_port(self) -> int:
        """Get the current port to use."""
        return self.state['current_port']

    def rotate_port(self) -> bool:
        """Rotate to next available port. Returns True if port available, False if exhausted."""
        current_port = self.state['current_port']
        blocked_ports = set(self.state['blocked_ports'])

        # Find next available port
        for port in range(current_port + 1, self.config.port_range_end + 1):
            if port not in blocked_ports:
                self.state['current_port'] = port
                self.state['cookies'] = {}  # Clear cookies for new IP
                self.state['challenge_solved'] = False
                self.state['session_start_time'] = None
                self._save_state()
                self.logger.info(f"Rotated to port {port}")
                return True

        self.logger.error("No more ports available in range")
        return False

    def mark_port_blocked(self, port: Optional[int] = None) -> None:
        """Mark a port as blocked."""
        port = port or self.state['current_port']
        if port not in self.state['blocked_ports']:
            self.state['blocked_ports'].append(port)
            self._save_state()
            self.logger.warning(f"Marked port {port} as blocked")

    def set_cookies(self, cookies: Dict[str, str]) -> None:
        """Save cookies from successful challenge solve."""
        self.state['cookies'] = cookies
        self.state['challenge_solved'] = True
        self.state['session_start_time'] = datetime.now().isoformat()
        self.state['last_success_time'] = datetime.now().isoformat()
        self._save_state()
        self.logger.info(f"Saved {len(cookies)} cookies for session")

    def get_cookies(self) -> Dict[str, str]:
        """Get current session cookies."""
        return self.state.get('cookies', {})

    def is_session_valid(self) -> bool:
        """Check if current session is still valid."""
        if not self.state.get('challenge_solved', False):
            return False

        # Check if cookies exist
        if not self.state.get('cookies'):
            return False

        # Check session age (invalidate after 2 hours)
        if self.state.get('session_start_time'):
            session_start = datetime.fromisoformat(self.state['session_start_time'])
            if datetime.now() - session_start > timedelta(hours=2):
                self.logger.info("Session expired due to age")
                return False

        return True

    def increment_request_count(self) -> None:
        """Increment request counter."""
        self.state['request_count'] = self.state.get('request_count', 0) + 1
        self.state['last_success_time'] = datetime.now().isoformat()
        self._save_state()

    def reset_session(self) -> None:
        """Reset current session (e.g., when blocked)."""
        self.state['cookies'] = {}
        self.state['challenge_solved'] = False
        self.state['session_start_time'] = None
        self._save_state()
        self.logger.info("Session reset")