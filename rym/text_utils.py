"""Text normalization utilities for RYM metadata processing."""

import re
import unicodedata


def normalize_text(text: str, *,
                  remove_accents: bool = True,
                  lowercase: bool = True,
                  remove_parentheticals: bool = False,
                  remove_punctuation: bool = False,
                  make_filesystem_safe: bool = False) -> str:
    """Normalize text with configurable features.

    Args:
        text: Input text to normalize
        remove_accents: Remove diacritical marks (NFD normalization)
        lowercase: Convert to lowercase
        remove_parentheticals: Remove content in parentheses like "(2023 remaster)"
        remove_punctuation: Remove non-word characters except spaces
        make_filesystem_safe: Replace/remove filesystem-unsafe characters

    Returns:
        Normalized text string
    """
    if not text:
        return ""

    result = text.strip()

    # Remove parentheticals (e.g., "(2023 remaster)", "(Deluxe Edition)")
    if remove_parentheticals:
        result = re.sub(r'\s*\([^)]*\)\s*', ' ', result)
        result = re.sub(r'\s*\[[^]]*\]\s*', ' ', result)
        result = re.sub(r'\s+', ' ', result).strip()

    # Remove accents using NFD normalization
    if remove_accents:
        result = unicodedata.normalize('NFD', result)
        result = ''.join(char for char in result if unicodedata.category(char) != 'Mn')

    # Convert to lowercase
    if lowercase:
        result = result.lower()

    # Remove punctuation (keep only word characters and spaces)
    if remove_punctuation:
        # First replace punctuation with spaces to preserve word boundaries
        result = re.sub(r'[^\w\s]', ' ', result)
    # Then normalize multiple spaces to single spaces
    result = re.sub(r'\s+', ' ', result).strip()

    # Make filesystem safe
    if make_filesystem_safe:
        # Replace invalid filename characters
        invalid_chars = r'[<>:"/\\|?*]'
        result = re.sub(invalid_chars, '_', result)

        # Replace spaces with underscores for readability
        result = re.sub(r'\s+', '_', result)

        # Truncate to reasonable filename length (200 chars)
        if len(result) > 200:
            result = result[:200].rstrip('_')

    return result