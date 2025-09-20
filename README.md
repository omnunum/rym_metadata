# Beets RateYourMusic Plugin

A beets plugin that scrapes genre information from RateYourMusic using the Bright Data API to bypass Cloudflare protection.

## Features

- Asynchronous processing with configurable concurrency
- Automatic retry logic for failed requests
- Search and match albums on RateYourMusic
- Extract and store genre information
- Configurable request timeouts and retry parameters

## Installation

1. Install the plugin and its dependencies:
```bash
pip install -r requirements.txt
pip install -e .
```

2. Add the plugin to your beets configuration file (`~/.config/beets/config.yaml`):
```yaml
plugins: rym

rym:
  brightdata_token: your_brightdata_token_here  # Optional, can use env var instead
  max_retries: 3                               # Number of retries for failed requests
  retry_delay: 2.0                            # Base delay between retries (seconds)
  concurrent_requests: 5                       # Max concurrent requests
  request_timeout: 30                          # Request timeout (seconds)
```

3. Set your Bright Data token as an environment variable:
```bash
export BRIGHTDATA_TOKEN="your_brightdata_token_here"
```

## Usage

### Basic Usage

Fetch RYM genre information for all albums:
```bash
beet rym
```

Fetch for specific albums:
```bash
beet rym artist:radiohead
beet rym album:"ok computer"
```

### Options

- `--force, -f`: Re-fetch genre info even if already present
- `--dry-run, -d`: Show what would be done without making changes

Examples:
```bash
# Force re-fetch for all albums
beet rym --force

# Dry run to see what albums would be processed
beet rym --dry-run artist:beethoven

# Force update specific album
beet rym --force album:"in rainbows"
```

## Configuration Options

### Bright Data API Version
| Option | Default | Description |
|--------|---------|-------------|
| `brightdata_token` | None | Bright Data API token (can also use BRIGHTDATA_TOKEN env var) |
| `max_retries` | 3 | Maximum number of retry attempts for failed requests |
| `retry_delay` | 2.0 | Base delay between retries in seconds (uses exponential backoff) |
| `concurrent_requests` | 5 | Maximum number of concurrent requests to RYM |
| `request_timeout` | 60 | Request timeout in seconds |

### Camoufox Version (Recommended)
| Option | Default | Description |
|--------|---------|-------------|
| `brightdata_user` | None | Bright Data proxy username (can also use BRIGHTDATA_USER env var) |
| `brightdata_pass` | None | Bright Data proxy password (can also use BRIGHTDATA_PASS env var) |
| `brightdata_endpoint` | See config | Bright Data proxy endpoint (must use port 33335 for new cert) |
| `proxy_cert_path` | None | Path to SSL certificate file (optional, can also use PROXY_CERT_PATH env var) |
| `session_type` | 'sticky' | Session control: 'sticky', 'rotate', or 'const' |
| `session_duration` | 600 | Session duration in seconds (for sticky sessions) |
| `max_retries` | 3 | Maximum number of retry attempts for failed requests |
| `retry_delay` | 2.0 | Base delay between retries in seconds |
| `concurrent_requests` | 2 | Maximum number of concurrent browser instances |
| `page_timeout` | 30000 | Page load timeout in milliseconds |

### Session Types
- **`sticky`**: Maintains same IP for configured duration, then rotates (recommended for CF bypass)
- **`rotate`**: Gets new IP for each request (use sparingly, may trigger more CF challenges)
- **`const`**: Uses same peer consistently (may fail if peer unavailable)

## How It Works

1. **Search**: The plugin searches RateYourMusic for each album using artist and album name
2. **Match**: It finds the best matching album page from search results
3. **Extract**: Genre information is extracted from the album page HTML
4. **Store**: Genres are stored in the `rym_genres` field of the album

## Data Storage

The plugin stores extracted genres in a new field called `rym_genres` as a semicolon-separated string. You can view this data with:

```bash
beet ls -f '$artist - $album: $rym_genres'
```

## Troubleshooting

### No Bright Data Token
If you see "No Bright Data token found", make sure you've set the `BRIGHTDATA_TOKEN` environment variable or configured `brightdata_token` in your beets config.

### Slow Performance
- Adjust `concurrent_requests` to increase/decrease parallelism
- Modify `request_timeout` if requests are timing out
- Check `max_retries` and `retry_delay` settings

### Missing Genres
- Use `--force` to re-fetch data for albums
- Check the beets log for any error messages
- Some albums may not have genre information on RYM

## Example Output

```
$ beet rym artist:radiohead
Processing 9 album(s)...
[1/9] Radiohead - OK Computer: Alternative Rock, Art Rock, Experimental Rock
[2/9] Radiohead - Kid A: Electronic, Experimental Rock, Art Rock
[3/9] Radiohead - In Rainbows: Alternative Rock, Art Rock
...
```