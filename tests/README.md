# RYM Metadata Scraper Tests

This directory contains comprehensive unit and integration tests for the RYM metadata scraper.

## Test Structure

```
tests/
├── __init__.py
├── conftest.py              # pytest fixtures and configuration
├── fixtures/
│   ├── cache/              # Real cached HTML responses from .rym_cache/
│   ├── html/               # Sample HTML responses for testing
│   └── configs/            # Test configurations
├── test_cache_manager.py    # Cache management tests
├── test_config.py          # Configuration validation tests
├── test_scraper_urls.py     # URL building tests
├── test_search.py          # Search and fuzzy matching tests
└── test_scraper.py         # Integration tests
```

## Running Tests

### Quick Start
```bash
# Run all tests
python run_tests.py

# Run with verbose output
python run_tests.py -v

# Run specific test file
python run_tests.py tests/test_cache_manager.py

# Run specific test
python run_tests.py tests/test_cache_manager.py::TestHtmlCacheManager::test_cache_creation
```

### Direct pytest
```bash
# Install test dependencies first
pip install -r requirements-test.txt

# Run tests
pytest tests/
```

## Test Categories

### 1. Cache Manager Tests (`test_cache_manager.py`)
- **Coverage**: HTML caching, expiration, cleanup
- **Mocking**: Uses real cache files from fixtures
- **Key features tested**:
  - Cache hit/miss scenarios
  - File corruption handling
  - Cache expiration logic
  - Cache statistics and cleanup

### 2. Configuration Tests (`test_config.py`)
- **Coverage**: Proxy configuration validation
- **Mocking**: Mock beets configuration objects
- **Key features tested**:
  - Configuration validation
  - URL building for different proxy types
  - Edge cases (empty strings, missing values)

### 3. URL Building Tests (`test_scraper_urls.py`)
- **Coverage**: Direct and search URL construction
- **Mocking**: Minimal (pure string manipulation)
- **Key features tested**:
  - Special character handling
  - Unicode support
  - URL encoding
  - Edge cases and real-world artist names

### 4. Search Engine Tests (`test_search.py`)
- **Coverage**: HTML parsing and fuzzy matching
- **Mocking**: Uses sample HTML and real cached data
- **Key features tested**:
  - Fuzzy matching algorithm
  - HTML parsing for search results
  - Year-based scoring
  - Best match selection

### 5. Scraper Integration Tests (`test_scraper.py`)
- **Coverage**: End-to-end scraping workflow
- **Mocking**: Async page objects, network calls
- **Key features tested**:
  - Genre extraction from HTML
  - Cache integration
  - Retry logic
  - Error handling

## Test Data Strategy

### Real Cache Data
Tests use real cached HTML responses from `.rym_cache/` copied to `tests/fixtures/cache/`. This provides:
- Realistic test scenarios
- Actual RYM HTML structure
- Edge cases from real data

### Sample HTML
For controlled testing, sample HTML is provided in fixtures with known structures.

### Mocking Strategy
- **External dependencies**: Mocked (network calls, browser interactions)
- **Business logic**: Tested with real data where possible
- **Configuration**: Mocked beets config objects

## Writing New Tests

### Adding a new test file:
1. Create `test_<module_name>.py`
2. Import fixtures from `conftest.py`
3. Use real cache data from fixtures when possible
4. Follow the naming convention: `test_<functionality>`

### Example test structure:
```python
class TestNewFeature:
    def test_basic_functionality(self, fixture_name):
        # Test basic case

    def test_edge_cases(self):
        # Test edge cases

    @pytest.mark.asyncio
    async def test_async_functionality(self, mock_page):
        # Test async methods
```

## Test Philosophy

These tests follow the principle of **"maximum signal, minimum maintenance"**:

1. **Focus on business logic**: Test the core functionality that's most likely to break
2. **Use real data**: Leverage existing cache files for realistic scenarios
3. **Minimal external dependencies**: Mock network calls but use real HTML parsing
4. **Fast execution**: No actual network calls, cached responses only
5. **High coverage of critical paths**: Cache management, URL building, HTML parsing, fuzzy matching

## Continuous Integration

Tests are designed to run in CI environments:
- No external network dependencies
- Deterministic with cached fixtures
- Fast execution (< 30 seconds typical)
- Clear failure messages with pytest's detailed output

## Troubleshooting

### Common issues:
- **Missing fixtures**: Ensure `.rym_cache/` files are copied to `tests/fixtures/cache/`
- **Import errors**: Install test dependencies with `pip install -r requirements-test.txt`
- **Async test failures**: Ensure `pytest-asyncio` is installed

### Debug mode:
```bash
# Run with maximum verbosity and stop on first failure
python run_tests.py -vvs -x

# Run with pdb on failures
python run_tests.py --pdb
```