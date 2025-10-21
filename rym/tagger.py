"""Audio file tagging utilities using mutagen.

This module provides shared functionality for reading and writing
metadata to audio files, used by both the CLI and beets plugin.
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import mutagen
    from mutagen.flac import FLAC
    from mutagen.mp3 import MP3
    from mutagen.mp4 import MP4
    from mutagen.oggvorbis import OggVorbis
    from mutagen.oggopus import OggOpus
    from mutagen.id3 import ID3, TCON, TXXX
except ImportError:
    raise ImportError(
        "mutagen is required for audio file tagging. "
        "Install it with: pip install mutagen"
    )

logger = logging.getLogger(__name__)

# Supported audio file extensions
AUDIO_EXTENSIONS = {'.flac', '.mp3', '.m4a', '.mp4', '.ogg', '.opus', '.wma', '.ape'}


def find_audio_files(folder: str, recursive: bool = True) -> List[str]:
    """Find all audio files in a folder.

    Args:
        folder: Path to folder to search
        recursive: Whether to search subdirectories

    Returns:
        List of absolute paths to audio files
    """
    folder_path = Path(folder)
    if not folder_path.exists():
        raise ValueError(f"Folder does not exist: {folder}")

    if not folder_path.is_dir():
        raise ValueError(f"Path is not a directory: {folder}")

    audio_files = []

    if recursive:
        for ext in AUDIO_EXTENSIONS:
            audio_files.extend(folder_path.rglob(f'*{ext}'))
    else:
        for ext in AUDIO_EXTENSIONS:
            audio_files.extend(folder_path.glob(f'*{ext}'))

    return sorted([str(f.absolute()) for f in audio_files])


def get_audio_metadata(file_path: str) -> Optional[Dict[str, any]]:
    """Read metadata from an audio file.

    Args:
        file_path: Path to audio file

    Returns:
        Dictionary with keys: artist, album, year, title, or None if file can't be read
    """
    try:
        audio = mutagen.File(file_path, easy=True)
        if audio is None:
            logger.warning(f"Could not read metadata from {file_path}")
            return None

        # Extract common fields (handle both single values and lists)
        def get_field(field_name: str) -> Optional[str]:
            value = audio.get(field_name, [None])
            if isinstance(value, list):
                return value[0] if value else None
            return value

        # Try different artist field names
        artist = (get_field('albumartist') or
                 get_field('artist') or
                 get_field('album artist'))

        album = get_field('album')
        title = get_field('title')

        # Try to get year from date field
        year = None
        date_str = get_field('date') or get_field('year')
        if date_str:
            try:
                # Extract year from date string (handle formats like "2023", "2023-01-01", etc.)
                year = int(str(date_str).split('-')[0])
            except (ValueError, AttributeError):
                pass

        return {
            'artist': artist,
            'album': album,
            'year': year,
            'title': title,
            'path': file_path
        }

    except Exception as e:
        logger.error(f"Error reading metadata from {file_path}: {e}")
        return None


def has_rym_metadata(file_path: str) -> bool:
    """Check if a file has already been processed by RYM tagger.

    We detect this by checking for the presence of the RYM_URL tag,
    which is always written when processing.

    Args:
        file_path: Path to audio file

    Returns:
        True if file has RYM_URL tag (already processed), False otherwise
    """
    try:
        file_ext = Path(file_path).suffix.lower()

        # For FLAC, OGG, Opus (Vorbis comments)
        if file_ext in {'.flac', '.ogg', '.opus'}:
            audio = mutagen.File(file_path)
            if audio is None:
                return False
            return 'RYM_URL' in audio or 'rym_url' in audio

        # For MP3 (ID3)
        elif file_ext == '.mp3':
            try:
                audio = MP3(file_path)
                if audio.tags is None:
                    return False
                # Check for TXXX:RYM_URL frame
                for frame in audio.tags.values():
                    if hasattr(frame, 'desc') and frame.desc == 'RYM_URL':
                        return True
                return False
            except:
                return False

        # For MP4/M4A
        elif file_ext in {'.m4a', '.mp4'}:
            audio = MP4(file_path)
            return '----:com.apple.iTunes:RYM_URL' in audio

        return False

    except Exception as e:
        logger.debug(f"Error checking RYM metadata for {file_path}: {e}")
        return False


def write_rym_metadata(file_path: str, genres: List[str], descriptors: List[str], rym_url: Optional[str] = None) -> bool:
    """Write RYM genres and descriptors to an audio file.

    Args:
        file_path: Path to audio file
        genres: List of genre strings
        descriptors: List of descriptor strings
        rym_url: RYM page URL (written to RYM_URL tag to mark file as processed)

    Returns:
        True if successful, False otherwise
    """
    file = Path(file_path)
    if not file.exists():
        logger.error(f"File does not exist: {file_path}")
        return False

    try:
        file_ext = file.suffix.lower()

        # Handle FLAC files (Vorbis Comments)
        if file_ext == '.flac':
            return _write_flac_tags(file_path, genres, descriptors, rym_url)

        # Handle MP3 files (ID3 tags)
        elif file_ext == '.mp3':
            return _write_mp3_tags(file_path, genres, descriptors, rym_url)

        # Handle M4A/MP4 files (MP4 tags)
        elif file_ext in {'.m4a', '.mp4'}:
            return _write_mp4_tags(file_path, genres, descriptors, rym_url)

        # Handle OGG Vorbis files
        elif file_ext == '.ogg':
            return _write_ogg_tags(file_path, genres, descriptors, rym_url)

        # Handle Opus files
        elif file_ext == '.opus':
            return _write_opus_tags(file_path, genres, descriptors, rym_url)

        else:
            logger.warning(f"Unsupported file format: {file_ext}")
            return False

    except Exception as e:
        logger.error(f"Error writing tags to {file_path}: {e}")
        return False


def _write_flac_tags(file_path: str, genres: List[str], descriptors: List[str], rym_url: Optional[str] = None) -> bool:
    """Write tags to FLAC file (Vorbis comments)."""
    try:
        audio = FLAC(file_path)

        if genres:
            # FLAC supports multiple GENRE tags
            audio['GENRE'] = genres

        if descriptors:
            # Use custom DESCRIPTOR tag (Vorbis comments allow arbitrary tags)
            audio['DESCRIPTOR'] = descriptors

        # Always write RYM_URL tag to mark as processed and provide reference
        if rym_url:
            audio['RYM_URL'] = rym_url

        audio.save()
        logger.info(f"Updated FLAC tags: {file_path}")
        return True

    except Exception as e:
        logger.error(f"Error writing FLAC tags to {file_path}: {e}")
        return False


def _write_mp3_tags(file_path: str, genres: List[str], descriptors: List[str], rym_url: Optional[str] = None) -> bool:
    """Write tags to MP3 file (ID3v2)."""
    try:
        # Load or create ID3 tags
        try:
            audio = MP3(file_path, ID3=ID3)
            if audio.tags is None:
                audio.add_tags()
        except mutagen.id3.ID3NoHeaderError:
            audio = MP3(file_path)
            audio.add_tags()

        if genres:
            # ID3v2: TCON frame for genre (semicolon-separated)
            audio.tags.add(TCON(encoding=3, text=genres))

        if descriptors:
            # ID3v2: Use TXXX frame for custom descriptor field
            audio.tags.add(TXXX(encoding=3, desc='DESCRIPTOR', text=descriptors))

        # Always write RYM_URL tag to mark as processed and provide reference
        if rym_url:
            audio.tags.add(TXXX(encoding=3, desc='RYM_URL', text=rym_url))

        audio.save()
        logger.info(f"Updated MP3 tags: {file_path}")
        return True

    except Exception as e:
        logger.error(f"Error writing MP3 tags to {file_path}: {e}")
        return False


def _write_mp4_tags(file_path: str, genres: List[str], descriptors: List[str], rym_url: Optional[str] = None) -> bool:
    """Write tags to MP4/M4A file."""
    try:
        audio = MP4(file_path)

        if genres:
            # MP4: Use \xa9gen atom for genre (list of strings)
            audio['\xa9gen'] = genres

        if descriptors:
            # MP4: Use custom ----:com.apple.iTunes:DESCRIPTOR atom
            # MP4 freeform atoms need bytes
            audio['----:com.apple.iTunes:DESCRIPTOR'] = [
                desc.encode('utf-8') for desc in descriptors
            ]

        # Always write RYM_URL tag to mark as processed and provide reference
        if rym_url:
            audio['----:com.apple.iTunes:RYM_URL'] = [rym_url.encode('utf-8')]

        audio.save()
        logger.info(f"Updated MP4 tags: {file_path}")
        return True

    except Exception as e:
        logger.error(f"Error writing MP4 tags to {file_path}: {e}")
        return False


def _write_ogg_tags(file_path: str, genres: List[str], descriptors: List[str], rym_url: Optional[str] = None) -> bool:
    """Write tags to OGG Vorbis file."""
    try:
        audio = OggVorbis(file_path)

        if genres:
            audio['GENRE'] = genres

        if descriptors:
            audio['DESCRIPTOR'] = descriptors

        # Always write RYM_URL tag to mark as processed and provide reference
        if rym_url:
            audio['RYM_URL'] = rym_url

        audio.save()
        logger.info(f"Updated OGG tags: {file_path}")
        return True

    except Exception as e:
        logger.error(f"Error writing OGG tags to {file_path}: {e}")
        return False


def _write_opus_tags(file_path: str, genres: List[str], descriptors: List[str], rym_url: Optional[str] = None) -> bool:
    """Write tags to Opus file."""
    try:
        audio = OggOpus(file_path)

        if genres:
            audio['GENRE'] = genres

        if descriptors:
            audio['DESCRIPTOR'] = descriptors

        # Always write RYM_URL tag to mark as processed and provide reference
        if rym_url:
            audio['RYM_URL'] = rym_url

        audio.save()
        logger.info(f"Updated Opus tags: {file_path}")
        return True

    except Exception as e:
        logger.error(f"Error writing Opus tags to {file_path}: {e}")
        return False


def group_files_by_album(file_paths: List[str]) -> Dict[Tuple[str, str], List[Dict]]:
    """Group audio files by album (artist + album name).

    Args:
        file_paths: List of audio file paths

    Returns:
        Dictionary mapping (artist, album) tuples to lists of file metadata dicts
    """
    albums = {}

    for file_path in file_paths:
        metadata = get_audio_metadata(file_path)
        if not metadata:
            continue

        artist = metadata.get('artist') or 'Unknown Artist'
        album = metadata.get('album') or 'Unknown Album'

        key = (artist, album)
        if key not in albums:
            albums[key] = []

        albums[key].append(metadata)

    return albums


def get_album_year(file_metadatas: List[Dict]) -> Optional[int]:
    """Get the year for an album from a list of file metadata.

    Args:
        file_metadatas: List of metadata dicts from files in the same album

    Returns:
        Year if found, None otherwise
    """
    # Find the first file with a year
    for metadata in file_metadatas:
        year = metadata.get('year')
        if year:
            return year
    return None
