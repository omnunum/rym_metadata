#!/usr/bin/env python3

from setuptools import setup, find_packages

setup(
    name='rym-metadata',
    version='1.4.1',
    description='RateYourMusic metadata scraper - standalone library and beets plugin',
    author='RYM Metadata',
    packages=find_packages(),
    py_modules=['beetsplug_rym_camoufox'],
    entry_points={
        'console_scripts': [
            'rym-tag=rym.cli:main',
        ],
    },
    install_requires=[
        # Core dependencies
        'aiohttp>=3.8.0',
        'beautifulsoup4>=4.11.0',
        'lxml>=4.9.0',
        'requests>=2.28.0',
        'urllib3>=1.26.0',

        # Browser automation
        'camoufox[geoip]>=0.3.0',
        'camoufox-captcha>=0.1.0',
        'playwright>=1.40.0',

        # Retry and resilience
        'tenacity>=8.0.0',

        # Audio file tagging
        'mutagen>=1.45.0',

        # Optional: beets for plugin functionality
        'beets>=1.6.0',

        # LLM matching (optional - gracefully degrades if not installed)
        'groq>=0.4.0',
    ],
    classifiers=[
        'Development Status :: 4 - Beta',
        'Environment :: Console',
        'Intended Audience :: End Users/Desktop',
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
        'Topic :: Multimedia :: Sound/Audio',
        'Topic :: Multimedia :: Sound/Audio :: Players',
    ],
    python_requires='>=3.8',
)