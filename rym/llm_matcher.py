"""LLM-based album matching using Groq API."""
import os
from typing import List, Optional
import logging

try:
    from groq import AsyncGroq
except ImportError:
    AsyncGroq = None

logger = logging.getLogger(__name__)


class GroqAlbumMatcher:
    """Match albums using Groq's fast LLM inference."""

    def __init__(self, api_key: Optional[str] = None):
        if AsyncGroq is None:
            logger.warning("groq-python not installed, LLM matching disabled")
            self.client = None
            return

        self.api_key = api_key or os.environ.get('GROQ_API_KEY')
        if not self.api_key:
            logger.info("No GROQ_API_KEY provided, LLM matching disabled")
            self.client = None
            return

        # Set explicit timeout (60 seconds total, 15 seconds for connection)
        # Increased for containerized environments with potential proxy latency
        from groq import Timeout
        import httpx

        # Create httpx client without proxy (Groq shouldn't use RYM proxy)
        http_client = httpx.AsyncClient(
            proxy=None,  # Explicitly disable proxy
            timeout=httpx.Timeout(60.0, connect=15.0)
        )

        self.client = AsyncGroq(
            api_key=self.api_key,
            http_client=http_client,
            max_retries=2  # Retry up to 2 times on failures
        )
        self.model = "llama-3.1-8b-instant"  # Fast, cheap model

    async def match_album(
        self,
        target_artist: str,
        target_album: str,
        candidates: List[dict]  # [{"album": "...", "year": ..., "url": "..."}]
    ) -> Optional[str]:
        """Use LLM to pick best matching album from candidates.

        Returns:
            URL of best match, or None if no good match
        """
        if not self.client:
            return None

        # Build prompt
        prompt = self._build_prompt(target_artist, target_album, candidates)

        try:
            logger.debug(f"Calling Groq API with model: {self.model}")
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,  # Deterministic
                max_tokens=10   # Just need a number
            )
            logger.debug(f"Groq API response received")

            choice = response.choices[0].message.content.strip()

            # Parse response (expecting just a number or "none")
            # Extract first number if LLM included extra text
            if choice.lower().startswith("none"):
                logger.info("LLM found no suitable match")
                return None

            try:
                # Try to extract just the number (handle cases like "2. Album Name")
                import re
                match = re.search(r'^\d+', choice)
                if match:
                    choice = match.group()

                idx = int(choice) - 1  # Convert 1-indexed to 0-indexed
                if 0 <= idx < len(candidates):
                    matched_album = candidates[idx]["album"]
                    logger.info(f"LLM matched to candidate #{int(choice)}: {matched_album}")
                    return candidates[idx]["url"]
                else:
                    logger.warning(f"LLM returned invalid index: {choice}")
            except (ValueError, AttributeError):
                logger.warning(f"LLM returned non-numeric response: {choice}")

            return None

        except Exception as e:
            # Log error but don't crash - fall back to artist search
            logger.error(f"LLM matching error: {e}")
            return None

    def _build_prompt(self, artist: str, album: str, candidates: List[dict]) -> str:
        """Build prompt for LLM matching."""
        prompt = f"""You are matching a music album to its entry on RateYourMusic.

Target album from metadata:
Artist: {artist}
Album: {album}

Possible matches from RateYourMusic:
"""
        for i, cand in enumerate(candidates[:10], 1):  # Limit to top 10
            year_str = f" ({cand['year']})" if cand.get('year') else ""
            prompt += f"{i}. {cand['album']}{year_str}\n"

        prompt += """
Which number is the best match? Consider:
- Album titles may use different formats and numbering conventions
- Match ALL volume numbers mentioned, not just the last one
- Artists may prepend their name to album titles
- Ignore year mismatches (metadata years are often wrong)

Examples of valid matches:
- "Volumes 7 & 8" matches "Desert Sessions 7 & 8" or "Vol VII/VIII" (both volumes present)
- "Volumes 7 & 8" does NOT match "Vol VIII" alone (missing Vol VII)
- "Volumes 1 & 2" matches "Vols. I/II" or "The Sessions 1 & 2"
- "Greatest Hits" matches "The Beatles: Greatest Hits" (artist name prepended)

CRITICAL: Respond with ONLY a single number (1-10) or the word "none".
Do NOT include the album name, year, or any other text. Just the number.
Examples of valid responses: "2" or "7" or "none"
Examples of INVALID responses: "2. Album Name" or "I think it's 2" """

        return prompt
