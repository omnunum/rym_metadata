# Integration Tests

This directory contains integration tests for the RYM metadata scraper that require network access and test the full system working together.

## Running Integration Tests

To run only the integration tests:

```bash
pytest integration_tests/ -m integration
```

To run all tests except integration tests:

```bash
pytest tests/ -m "not integration"
```

To run all tests including integration tests:

```bash
pytest tests/ integration_tests/
```

## Test Files

- `test_genre_loading.py` - Tests enhanced genre loading and parent expansion functionality
- `test_genre_hierarchy.py` - Tests genre hierarchy system with interactive components
- `test_rate_limiting.py` - Tests rate limiting behavior and error handling
- `test_proxy_config.py` - Tests proxy configuration and IP rotation

## Notes

- Integration tests may take longer to run as they make real network requests
- Some tests may require specific environment variables for proxy configuration
- These tests are marked with `@pytest.mark.integration` to allow selective execution
- Consider running integration tests separately from unit tests in CI/CD pipelines