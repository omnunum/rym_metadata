# Beets RateYourMusic Plugin

A beets plugin that scrapes genre information from RateYourMusic using Camoufox browser automation with proxy support for Cloudflare bypass.

## Installation

```bash
pip install -r requirements.txt
pip install -e .
```

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
```

## Configuration Options

| Option | Default | Description |
|--------|---------|-------------|
| `proxy_enabled` | true | Enable/disable proxy usage |
| `proxy_host` | None | Proxy server hostname |
| `proxy_port` | None | Proxy server port |
| `proxy_username` | None | Proxy authentication username |
| `proxy_password` | None | Proxy authentication password |
| `proxy_use_tls` | false | Use HTTPS for proxy connection |
| `max_retries` | 3 | Number of retry attempts |
| `page_timeout` | 30000 | Page load timeout (milliseconds) |
| `cache_enabled` | true | Enable HTML caching |
| `cache_dir` | .rym_cache | Cache directory path |
| `auto_tag` | false | Automatically tag albums |

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

## Data Storage

Genres are stored in the `rym_genres` field. View with:
```bash
beet ls -f '$artist - $album: $rym_genres'
```