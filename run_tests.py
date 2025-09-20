#!/usr/bin/env python3
"""Simple test runner for RYM metadata scraper tests."""

import subprocess
import sys
from pathlib import Path


def main():
    """Run the test suite."""
    project_root = Path(__file__).parent

    # Install test dependencies if not already installed
    try:
        import pytest
        import pytest_asyncio
    except ImportError:
        print("Installing test dependencies...")
        subprocess.check_call([
            sys.executable, "-m", "pip", "install", "-r",
            str(project_root / "requirements-test.txt")
        ])

    # Change to project directory
    import os
    os.chdir(project_root)

    # Run pytest with configuration
    cmd = [sys.executable, "-m", "pytest", "tests/"]

    # Add any command line arguments passed to this script
    if len(sys.argv) > 1:
        cmd.extend(sys.argv[1:])

    print(f"Running: {' '.join(cmd)}")
    sys.exit(subprocess.call(cmd))


if __name__ == "__main__":
    main()