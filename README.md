# RYM Metadata Scraper

A flexible RateYourMusic metadata scraper that can be used as a beets plugin, standalone library, or CLI tool. Scrapes genre and descriptor information using Camoufox browser automation with proxy support for Cloudflare bypass.

## Usage Modes

This package supports three usage patterns:
1. **CLI Tool**: Standalone command-line tool for tagging audio files in folders
2. **Beets Plugin**: Integrates with beets music library management
3. **Standalone Library**: Can be imported into other tools (like streamrip forks)

## Installation

```bash
pip install -r requirements.txt
pip install -e .
```

## Usage Patterns

### 1. CLI Tool

The `rym-tag` command-line tool allows you to tag audio files in a folder with RYM metadata.

#### Basic Usage

```bash
# Tag all audio files in a folder (recursively)
rym-tag /path/to/music/folder

# Dry run to see what would be tagged
rym-tag /path/to/music/folder --dry-run

# Force re-tagging of already processed files
rym-tag /path/to/music/folder --force

# Non-recursive (only files in the specified folder)
rym-tag /path/to/music/folder --no-recursive

# Clear cache
rym-tag --clear-cache

# Show cache information
rym-tag --cache-info
```

#### How It Works

1. Scans folder for audio files (FLAC, MP3, M4A, OGG, Opus, etc.)
2. Groups files by album (using artist + album tags from files)
3. Fetches RYM metadata for each album
4. Writes genres and descriptors to audio file tags
5. Marks files as processed (by writing DESCRIPTOR tag)
6. Skips already-processed files on subsequent runs (unless `--force`)

#### Proxy Configuration

Set proxy credentials via environment variables or command-line arguments:

```bash
# Using environment variables
export PROXY_HOST=proxy.example.com
export PROXY_PORT=8080
export PROXY_USERNAME=your_username
export PROXY_PASSWORD=your_password
rym-tag /path/to/music

# Using command-line arguments
rym-tag /path/to/music --proxy-host proxy.example.com --proxy-port 8080 \
  --proxy-username your_username --proxy-password your_password

# Disable proxy (not recommended for RYM)
rym-tag /path/to/music --no-proxy
```

#### Tag Format

The CLI writes the following tags to audio files:

- **GENRE** / **TCON**: Genre list (e.g., "Electronic", "Techno", "Acid")
- **DESCRIPTOR** / **TXXX:DESCRIPTOR**: Descriptor list (e.g., "hypnotic", "energetic") - only if descriptors found
- **RYM_URL** / **TXXX:RYM_URL**: RateYourMusic URL for the album or artist

The RYM_URL tag is **always written** to mark files as processed and provide a reference link. This allows the tool to skip already-tagged files on subsequent runs.

#### Supported Audio Formats

- FLAC (Vorbis Comments)
- MP3 (ID3v2)
- M4A/MP4 (MP4 atoms)
- OGG Vorbis
- Opus

### 2. Standalone Library (for streamrip, etc.)

The standalone API is designed to be imported into any Python application without requiring beets.

#### Basic Setup

```python
import asyncio
from rym import RYMMetadataScraper, RYMConfig, RYMMetadata

# Create configuration
config = RYMConfig(
    proxy_enabled=True,  # Note: defaults to False
    proxy_host="your.proxy.host",
    proxy_port=8080,
    proxy_username="your_username",
    proxy_password="your_password",

    # Optional settings
    cache_enabled=True,
    cache_dir=".rym_cache",
    max_retries=3
)

# Create scraper
scraper = RYMMetadataScraper(config)
```

#### Single Album Lookup

```python
async def get_single_album():
    # Include year for better matching when available
    metadata = await scraper.get_album_metadata("Radiohead", "OK Computer", 1997)

    if metadata:
        print(f"Genres: {metadata.genres}")           # ['Alternative Rock', 'Art Rock']
        print(f"Descriptors: {metadata.descriptors}") # ['melancholic', 'atmospheric']
        print(f"URL: {metadata.url}")                  # RYM album page URL
    else:
        print("Album not found on RYM")

# Artist lookup
async def get_single_artist():
    artist_metadata = await scraper.get_artist_metadata("Radiohead")
    if artist_metadata:
        print(f"Artist Genres: {artist_metadata.genres}")
        print(f"Artist URL: {artist_metadata.url}")
    else:
        print("Artist not found on RYM")
```

#### Batch Processing

```python
async def get_multiple_albums():
    albums = [
        ("Radiohead", "Kid A", 2000),
        ("Aphex Twin", "Selected Ambient Works 85-92", 1992),
        ("Artist Name", "Album Name", None)  # Year can be None
    ]

    results = await scraper.get_multiple_albums_metadata(albums)

    for i, metadata in enumerate(results):
        artist, album, year = albums[i]
        if metadata:
            genres_str = ", ".join(metadata.genres)
            desc_str = ", ".join(metadata.descriptors)
            print(f"{artist} - {album}: {genres_str} | {desc_str}")
        else:
            print(f"{artist} - {album}: Not found")
```

#### Running Standalone Scripts

```python
import asyncio

async def main():
    config = RYMConfig(proxy_enabled=True, ...)
    scraper = RYMMetadataScraper(config)

    metadata = await scraper.get_album_metadata("Artist", "Album", 2000)
    return metadata

# Run the async function
if __name__ == "__main__":
    result = asyncio.run(main())

# Recommended: Use context manager for automatic cleanup
async def main_with_context():
    config = RYMConfig(proxy_enabled=True, ...)

    async with RYMMetadataScraper(config) as scraper:
        metadata = await scraper.get_album_metadata("Artist", "Album", 2000)
        return metadata

# Run with context manager
if __name__ == "__main__":
    result = asyncio.run(main_with_context())
```

#### Configuration Options for Standalone

```python
config = RYMConfig(
    # Proxy settings (usually required for Cloudflare bypass)
    proxy_enabled=True,
    proxy_host="proxy.example.com",
    proxy_port=8080,
    proxy_username="username",
    proxy_password="password",
    proxy_use_tls=False,                    # True for HTTPS proxy

    # Proxy rotation method
    proxy_rotation_method='port',           # 'port' or 'username' - how IPs are rotated (default: 'port')
    auto_rotate_on_failure=True,            # Auto-rotate when proxy errors occur (default: True)

    # Session management (controls timing/request patterns)
    session_type='const',                   # 'const', 'sticky', 'rotate' (default: 'const')
    session_duration=600,                   # Seconds to keep same session (for sticky)

    # Caching (improves performance)
    cache_enabled=True,
    cache_dir=".rym_cache",
    cache_expiry_days=7,                    # 0 = never expires (default: 0)

    # Session persistence (for external programs)
    session_state_file_path="/path/to/your/app/.rym_session.json",  # Optional: custom session file location

    # Retry behavior
    max_retries=3,
    retry_delay=2.0,                        # Base delay between retries
    page_timeout=30000,                     # Page load timeout (ms)

    # Rate limiting (helps avoid getting blocked)
    min_request_interval=3.0,               # Minimum seconds between requests (0 = disabled)
    humanize_request_interval=True,         # Add ±25% random jitter

    # Bandwidth optimization
    resource_blocking_enabled=True,         # Block images/CSS for speed

    # Search matching
    matching_threshold=0.85                  # Minimum similarity score (0.0-1.0) for accepting matches
)
```

#### Error Handling

```python
async def safe_lookup(artist, album, year=None):
    try:
        scraper = RYMMetadataScraper(config)
        metadata = await scraper.get_album_metadata(artist, album, year)
        return metadata
    except Exception as e:
        print(f"Error looking up {artist} - {album}: {e}")
        return None
```

### 3. Beets Plugin

Add to beets config (`~/.config/beets/config.yaml`):
```yaml
plugins: rym

rym:
  # Proxy configuration (required for Cloudflare bypass)
  proxy_enabled: true
  proxy_host: your.proxy.host
  proxy_port: 8080
  proxy_username: your_username
  proxy_password: your_password
  proxy_use_tls: false

  # Optional settings
  max_retries: 3
  page_timeout: 30000
  cache_enabled: true
  auto_tag: false
  matching_threshold: 0.85

  # NEW: Write tags directly to audio files (enables descriptors in files)
  write_tags_to_files: false  # Set to true to write genres/descriptors directly to audio files
```

#### Writing Tags to Files

By default, the beets plugin stores metadata in the beets database:
- **Genres**: Written to both database AND audio files (beets native support)
- **Descriptors**: Written to database ONLY (beets doesn't support custom tags)

**Enable `write_tags_to_files`** to write both genres and descriptors directly to audio files using mutagen:

```yaml
rym:
  write_tags_to_files: true  # Enables direct file tagging
  # ... other settings
```

When enabled:
- Genres are written to the `GENRE`/`TCON` tag
- Descriptors are written to the `DESCRIPTOR` tag (custom field)
- Works with FLAC, MP3, M4A, OGG, Opus
- Descriptors are now preserved in the actual audio files

This is useful if you want to:
- Keep descriptors when moving files outside of beets
- Use descriptors in other music players/tools
- Have complete metadata embedded in files

## Proxy Rotation Methods

**Port-based rotation** (`proxy_rotation_method='port'`):
- Uses port rotation for IP changes (e.g., ports 10001-10100)
- Sends clean username to proxy
- Common with services that use port-based IP assignment

**Username-based rotation** (`proxy_rotation_method='username'`):
- Uses username suffixes for IP control (e.g., `user-const`, `user-session123`)
- Keeps same port
- Common with services like Bright Data

**Session types** control timing/request patterns:
- `'const'`: Consistent session behavior
- `'sticky'`: Same session for duration, then change
- `'rotate'`: New session per request

**Rate limiting** helps avoid getting blocked:
- `min_request_interval`: Minimum time between requests (default: 3 seconds)
- `humanize_request_interval`: Adds ±25% jitter to look more human (default: enabled)

**Examples:**
```python
# Port-based proxy (e.g., rotating proxy with port-based IPs)
config = RYMConfig(
    proxy_rotation_method='port',
    proxy_host="proxy.example.com",
    proxy_port=10001,  # Starting port
    port_range_start=10001,
    port_range_end=10100
)

# Username-based proxy (e.g., Bright Data)
config = RYMConfig(
    proxy_rotation_method='username',
    proxy_host="proxy.brightdata.com",
    proxy_port=8080,  # Single port
    session_type='sticky'  # Controls username suffix timing
)
```

## Configuration Options

| Option | Default | Description |
|--------|---------|-------------|
| `proxy_enabled` | false | Enable/disable proxy usage |
| `proxy_host` | None | Proxy server hostname |
| `proxy_port` | None | Proxy server port |
| `proxy_username` | None | Proxy authentication username |
| `proxy_password` | None | Proxy authentication password |
| `proxy_use_tls` | false | Use HTTPS for proxy connection |
| `proxy_rotation_method` | port | How IPs are rotated ('port' or 'username') |
| `auto_rotate_on_failure` | true | Auto-rotate when proxy errors occur |
| `session_type` | const | Session timing pattern ('const', 'sticky', 'rotate') |
| `max_retries` | 3 | Number of retry attempts |
| `page_timeout` | 30000 | Page load timeout (milliseconds) |
| `min_request_interval` | 3.0 | Minimum seconds between requests (0 = disabled) |
| `humanize_request_interval` | true | Add ±25% random jitter to request intervals |
| `cache_enabled` | true | Enable HTML caching |
| `cache_dir` | .rym_cache | Cache directory path |
| `session_state_file_path` | None | Custom path for session state file (defaults to .rym_session_state.json in current directory) |
| `auto_tag` | false | Automatically tag albums during import |
| `matching_threshold` | 0.85 | Minimum similarity score (0.0-1.0) for accepting matches |

## Usage

```bash
beet rym                       # Process all albums
beet rym artist:radiohead      # Process specific artist
beet rym album:"ok computer"   # Process specific album
beet rym --force               # Re-fetch existing data
beet rym --dry-run             # Preview changes without saving
beet rym --debug               # Enable debug logging
beet rym --clear-cache         # Clear HTML cache
beet rym --cache-info          # Show cache statistics
```

## Auto-Tagging

Set `auto_tag: true` in your config to automatically fetch RYM genres when importing albums:

```yaml
rym:
  auto_tag: true
  # ... other config options
```

This will automatically add RYM genre information to newly imported albums.

## Data Fields

### Standalone Usage
**RYMMetadata:**
- `metadata.artist`: Artist name
- `metadata.genres`: List of genre strings
- `metadata.descriptors`: List of descriptor strings
- `metadata.url`: RYM page URL
- `metadata.album`: Album name (None for artist-only metadata)
- `metadata.album_type`: Album type ("album", "single", "ep", "compilation")

### Beets Plugin
- `genres`: Semicolon-separated genres (written to files)
- `descriptors`: Semicolon-separated descriptors (beets database only)

View beets data with:
```bash
beet ls -f '$artist - $album: $genres'
beet ls -f '$artist - $album: $descriptors'
```

## Session Persistence for External Programs

When importing RYM scraper into external programs, configure a consistent session file path to avoid repeated Cloudflare challenge solving:

```python
from rym import RYMMetadataScraper, RYMConfig

# Configure session file path for your application
config = RYMConfig(
    proxy_enabled=True,
    proxy_host="your.proxy.host",
    proxy_port=8080,
    proxy_username="your_username",
    proxy_password="your_password",
    # This ensures cookies persist across runs from different directories
    session_state_file_path="/path/to/your/app/.rym_session.json"
)

async with RYMMetadataScraper(config) as scraper:
    # Subsequent runs will reuse saved cookies instead of solving challenges
    metadata = await scraper.get_album_metadata("Artist", "Album", 2000)
```

**Benefits:**
- Avoids repeated Cloudflare challenge solving across program runs
- Works regardless of current working directory
- Shared session state between different scripts in your application

## Streamrip Integration Example

Basic integration pattern:

```python
from rym import RYMMetadataScraper, RYMConfig
from mutagen.flac import FLAC

async def enhance_audio_file(artist, album, file_path):
    scraper = RYMMetadataScraper(config)
    metadata = await scraper.get_album_metadata(artist, album)

    if metadata:
        audio = FLAC(file_path)
        audio['GENRE'] = metadata.genres
        audio['DESCRIPTORS'] = metadata.descriptors  # Custom field
        audio.save()
```

## Quick Start (Standalone)

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Test the setup:**
   ```bash
   python example_standalone.py
   ```

3. **Set up proxy credentials** (required for bypassing Cloudflare):
   - Get proxy credentials from a service like Bright Data
   - Update config with your proxy details

4. **Basic test script:**
   ```python
   import asyncio
   from rym import RYMMetadataScraper, RYMConfig

   async def test():
       config = RYMConfig(
           proxy_enabled=True,
           proxy_host="your.proxy.host",
           proxy_port=8080,
           proxy_username="your_username",
           proxy_password="your_password"
       )

       scraper = RYMMetadataScraper(config)
       result = await scraper.get_album_metadata("Radiohead", "OK Computer", 1997)

       if result:
           print("Success!")
           print(f"Genres: {result.genres}")
           print(f"Descriptors: {result.descriptors}")
       else:
           print("Failed to get metadata")

   asyncio.run(test())
   ```