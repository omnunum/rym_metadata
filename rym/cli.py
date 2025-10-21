#!/usr/bin/env python3
"""Command-line interface for RYM metadata tagging.

This module provides a standalone CLI tool for tagging audio files
in a folder with RateYourMusic metadata.
"""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Optional

from rym import __version__
from rym.core import RYMMetadataScraper
from rym.dataclasses import RYMConfig
from rym.tagger import (
    find_audio_files,
    group_files_by_album,
    get_album_year,
    write_rym_metadata,
    has_rym_metadata
)


def setup_logging(debug: bool = False):
    """Configure logging for CLI."""
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(levelname)s: %(message)s'
    )


def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description='Tag audio files with RateYourMusic metadata',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s /path/to/music
  %(prog)s /path/to/music --dry-run
  %(prog)s /path/to/music --force --recursive
  %(prog)s /path/to/music --no-recursive
  %(prog)s --clear-cache
  %(prog)s --cache-info

Environment Variables:
  PROXY_HOST      Proxy server hostname
  PROXY_PORT      Proxy server port
  PROXY_USERNAME  Proxy authentication username
  PROXY_PASSWORD  Proxy authentication password
        """
    )

    parser.add_argument(
        '--version',
        action='version',
        version=f'%(prog)s {__version__}'
    )

    parser.add_argument(
        'folder',
        nargs='?',
        help='Folder containing audio files to tag'
    )

    parser.add_argument(
        '-f', '--force',
        action='store_true',
        help='Re-fetch and overwrite existing RYM metadata'
    )

    parser.add_argument(
        '-d', '--dry-run',
        action='store_true',
        help='Show what would be done without making changes'
    )

    parser.add_argument(
        '-r', '--recursive',
        action='store_true',
        default=True,
        help='Search subdirectories recursively (default: enabled)'
    )

    parser.add_argument(
        '--no-recursive',
        dest='recursive',
        action='store_false',
        help='Do not search subdirectories'
    )

    parser.add_argument(
        '--debug',
        action='store_true',
        help='Enable debug logging'
    )

    parser.add_argument(
        '--clear-cache',
        action='store_true',
        help='Clear cached HTML data and exit'
    )

    parser.add_argument(
        '--cache-info',
        action='store_true',
        help='Show cache statistics and exit'
    )

    # Proxy configuration
    proxy_group = parser.add_argument_group('proxy options')
    proxy_group.add_argument(
        '--proxy-host',
        help='Proxy server hostname (default: from PROXY_HOST env var)'
    )
    proxy_group.add_argument(
        '--proxy-port',
        type=int,
        help='Proxy server port (default: from PROXY_PORT env var)'
    )
    proxy_group.add_argument(
        '--proxy-username',
        help='Proxy username (default: from PROXY_USERNAME env var)'
    )
    proxy_group.add_argument(
        '--proxy-password',
        help='Proxy password (default: from PROXY_PASSWORD env var)'
    )
    proxy_group.add_argument(
        '--no-proxy',
        action='store_true',
        help='Disable proxy usage (use direct connection)'
    )

    return parser.parse_args()


def create_config_from_args(args) -> RYMConfig:
    """Create RYMConfig from command-line arguments and environment variables."""
    # Get proxy settings from args or environment
    proxy_host = args.proxy_host or os.environ.get('PROXY_HOST')
    proxy_port = args.proxy_port or (
        int(os.environ.get('PROXY_PORT')) if os.environ.get('PROXY_PORT') else None
    )
    proxy_username = args.proxy_username or os.environ.get('PROXY_USERNAME')
    proxy_password = args.proxy_password or os.environ.get('PROXY_PASSWORD')

    # Determine if proxy should be enabled
    proxy_enabled = not args.no_proxy and all([
        proxy_host, proxy_port, proxy_username, proxy_password
    ])

    return RYMConfig(
        proxy_enabled=proxy_enabled,
        proxy_host=proxy_host,
        proxy_port=proxy_port,
        proxy_username=proxy_username,
        proxy_password=proxy_password,
        cache_enabled=True,
        cache_dir='.rym_cache',
        headless=True,
    )


async def process_folder(folder: str, config: RYMConfig, force: bool = False,
                        dry_run: bool = False, recursive: bool = True):
    """Process all audio files in a folder.

    Args:
        folder: Path to folder containing audio files
        config: RYM configuration
        force: Re-fetch even if metadata exists
        dry_run: Show what would be done without making changes
        recursive: Search subdirectories
    """
    logger = logging.getLogger(__name__)

    # Find audio files
    print(f"Scanning for audio files in: {folder}")
    try:
        audio_files = find_audio_files(folder, recursive=recursive)
    except ValueError as e:
        print(f"Error: {e}")
        return

    if not audio_files:
        print("No audio files found")
        return

    print(f"Found {len(audio_files)} audio file(s)")

    # Group by album
    print("Grouping files by album...")
    albums = group_files_by_album(audio_files)

    if not albums:
        print("No albums found (files may be missing artist/album tags)")
        return

    print(f"Found {len(albums)} album(s)")

    if dry_run:
        print("\n--- DRY RUN MODE (no changes will be made) ---\n")

    # Process each album
    processed_count = 0
    failed_count = 0
    skipped_count = 0

    async with RYMMetadataScraper(config) as scraper:
        for i, ((artist, album), file_metadatas) in enumerate(albums.items(), 1):
            print(f"\n[{i}/{len(albums)}] {artist} - {album}")

            # Check if any file in this album already has RYM metadata (unless --force)
            if not force:
                already_processed = any(
                    has_rym_metadata(fm['path']) for fm in file_metadatas
                )
                if already_processed:
                    print(f"  ⊘ Skipping (already processed, use --force to override)")
                    skipped_count += 1
                    continue

            # Get year from first file with year metadata
            year = get_album_year(file_metadatas)

            try:
                # Fetch RYM metadata
                print(f"  Fetching RYM metadata...")
                rym_metadata = await scraper.get_album_metadata(artist, album, year)

                if not rym_metadata:
                    # Try artist fallback
                    print(f"  Album not found, trying artist fallback...")
                    rym_metadata = await scraper.get_artist_metadata(artist)

                if not rym_metadata:
                    print(f"  ✗ No RYM data found")
                    failed_count += 1
                    continue

                genres = rym_metadata.genres
                descriptors = rym_metadata.descriptors
                rym_url = rym_metadata.url

                # Display what was found
                if genres:
                    print(f"  Genres: {', '.join(genres)}")
                if descriptors:
                    print(f"  Descriptors: {', '.join(descriptors)}")
                if rym_url:
                    print(f"  URL: {rym_url}")

                if not genres and not descriptors:
                    print(f"  ✗ No genres or descriptors found")
                    failed_count += 1
                    continue

                # Write to files
                if not dry_run:
                    print(f"  Writing tags to {len(file_metadatas)} file(s)...")
                    success_count = 0
                    for file_metadata in file_metadatas:
                        file_path = file_metadata['path']
                        if write_rym_metadata(file_path, genres, descriptors, rym_url):
                            success_count += 1

                    if success_count == len(file_metadatas):
                        print(f"  ✓ Successfully tagged {success_count} file(s)")
                        processed_count += 1
                    else:
                        print(f"  ⚠ Tagged {success_count}/{len(file_metadatas)} file(s)")
                        if success_count > 0:
                            processed_count += 1
                else:
                    print(f"  [DRY RUN] Would tag {len(file_metadatas)} file(s)")
                    processed_count += 1

            except Exception as e:
                print(f"  ✗ Error: {e}")
                logger.error(f"Error processing {artist} - {album}: {e}")
                failed_count += 1

    # Summary
    print(f"\n{'='*60}")
    print(f"Summary:")
    print(f"  Processed: {processed_count}/{len(albums)}")
    if skipped_count > 0:
        print(f"  Skipped: {skipped_count}")
    if failed_count > 0:
        print(f"  Failed: {failed_count}")
    if dry_run:
        print(f"\n  (DRY RUN - no changes were made)")
    print(f"{'='*60}")


def main():
    """Main CLI entry point."""
    args = parse_args()
    setup_logging(args.debug)
    logger = logging.getLogger(__name__)

    # Create config
    config = create_config_from_args(args)

    # Handle cache management commands
    if args.cache_info or args.clear_cache:
        scraper = RYMMetadataScraper(config)

        if args.cache_info:
            cache_info = scraper.get_cache_info()
            if cache_info.get('cache_enabled'):
                print(f"Cache directory: {cache_info.get('cache_dir', 'N/A')}")
                print(f"Total cached files: {cache_info.get('total_files', 0)}")
                print(f"Total cache size: {cache_info.get('total_size_mb', 0):.2f} MB")
                print(f"Cache expiry: {cache_info.get('expiry_days', 0)} days", end='')
                if cache_info.get('expiry_days', 0) == 0:
                    print(" (never expires)")
                else:
                    print()
                if cache_info.get('expired_files', 0) > 0:
                    print(f"Expired files: {cache_info.get('expired_files', 0)}")
            else:
                print("Cache is disabled")
            return 0

        if args.clear_cache:
            cleared_count = scraper.clear_cache()
            print(f"Cleared {cleared_count} cache file(s)")
            return 0

    # Require folder argument for normal operation
    if not args.folder:
        print("Error: folder argument is required", file=sys.stderr)
        print("Use --help for usage information", file=sys.stderr)
        return 1

    # Validate folder
    folder_path = Path(args.folder)
    if not folder_path.exists():
        print(f"Error: folder does not exist: {args.folder}", file=sys.stderr)
        return 1

    if not folder_path.is_dir():
        print(f"Error: path is not a directory: {args.folder}", file=sys.stderr)
        return 1

    # Check proxy configuration
    if not config.proxy_enabled:
        logger.warning(
            "No proxy configured. RYM access may fail due to Cloudflare protection."
        )
        logger.warning(
            "Set proxy via PROXY_* environment variables or --proxy-* arguments."
        )

    # Process folder
    try:
        asyncio.run(process_folder(
            args.folder,
            config,
            force=args.force,
            dry_run=args.dry_run,
            recursive=args.recursive
        ))
        return 0

    except KeyboardInterrupt:
        print("\nInterrupted by user")
        return 130

    except Exception as e:
        logger.error(f"Fatal error: {e}")
        if args.debug:
            raise
        return 1


if __name__ == '__main__':
    sys.exit(main())
