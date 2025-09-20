#!/usr/bin/env python3

from setuptools import setup

setup(
    name='beets-rym',
    version='1.0.0',
    description='RateYourMusic genre scraper plugin for beets',
    author='RYM Metadata',
    py_modules=['beetsplug_rym'],
    install_requires=[
        'beets>=1.6.0',
        'aiohttp>=3.8.0',
        'beautifulsoup4>=4.11.0',
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