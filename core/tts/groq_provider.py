"""
Groq-hosted Orpheus TTS provider (English narration).

Docs: https://console.groq.com/docs/text-to-speech
Endpoint: POST https://api.groq.com/openai/v1/audio/speech

IMPORTANT: `canopylabs/orpheus-v1-english` is English-only. Groq also hosts
`canopylabs/orpheus-arabic-saudi` for Saudi Arabic; if you want to use it,
instantiate `GroqTTSProvider(model="canopylabs/orpheus-arabic-saudi")` and
register it for "ar" in `core/tts/factory.py`. All other languages should
route to a different provider (see `google_provider.py`).
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Optional

import requests

from .base import TTSProvider, TTSRateLimitError, TTSRequestError

logger = logging.getLogger("story_video_generator")

GROQ_TTS_URL = "https://api.groq.com/openai/v1/audio/speech"
DEFAULT_MODEL = "canopylabs/orpheus-v1-english"
DEFAULT_TIMEOUT_S = 30
MAX_RETRIES = 5
BASE_BACKOFF_SECONDS = 1.5


class GroqTTSProvider(TTSProvider):
    """English narration via Groq's Orpheus v1 English model."""

    name = "groq-orpheus-english"

    def __init__(self, model: str = DEFAULT_MODEL, api_key: Optional[str] = None,
                 timeout: int = DEFAULT_TIMEOUT_S):
        self.model = model
        self.timeout = timeout
        # Never hardcode the key -- always environment / .env (see README).
        self.api_key = api_key or os.environ.get("GROQ_API_KEY")
        if not self.api_key:
            raise TTSRequestError(
                "GROQ_API_KEY is not set. Export it in your shell or add it "
                "to a .env file at the project root. Get a key at "
                "https://console.groq.com/keys (see README for setup)."
            )

    def supports_language(self, language: str) -> bool:
        # orpheus-v1-english is English-only; if you swap `model` to the
        # Arabic Saudi variant, override this accordingly.
        if self.model == DEFAULT_MODEL:
            return language.split("-")[0].lower() == "en"
        if "arabic" in self.model:
            return language.split("-")[0].lower() == "ar"
        return True

    def synthesize(self, text: str, language: str, voice: str, *, out_path: Path) -> Path:
        if not self.supports_language(language):
            raise TTSRequestError(
                f"GroqTTSProvider (model={self.model}) does not support "
                f"language '{language}'. See factory.py for provider routing."
            )

        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "model": self.model,
            "input": text,
            "voice": voice,
            "response_format": "wav",
            "speed": 1.0,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        last_error: Optional[Exception] = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = requests.post(
                    GROQ_TTS_URL, headers=headers, json=payload, timeout=self.timeout
                )

                if response.status_code == 429:
                    retry_after = float(
                        response.headers.get("Retry-After", BASE_BACKOFF_SECONDS * attempt)
                    )
                    logger.warning(
                        "Groq TTS rate-limited (attempt %d/%d). Waiting %.1fs.",
                        attempt, MAX_RETRIES, retry_after,
                    )
                    time.sleep(retry_after)
                    last_error = TTSRateLimitError(response.text)
                    continue

                if response.status_code == 401:
                    raise TTSRequestError(
                        "Groq TTS request rejected (401 Unauthorized). Verify "
                        "GROQ_API_KEY is correct and that you've accepted the "
                        "model terms for canopylabs/orpheus-v1-english at "
                        "https://console.groq.com/playground?model=canopylabs/orpheus-v1-english"
                    )

                if response.status_code >= 500:
                    backoff = BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
                    logger.warning(
                        "Groq TTS server error %d (attempt %d/%d). Retrying in %.1fs.",
                        response.status_code, attempt, MAX_RETRIES, backoff,
                    )
                    time.sleep(backoff)
                    last_error = TTSRequestError(f"HTTP {response.status_code}: {response.text[:300]}")
                    continue

                if response.status_code >= 400:
                    raise TTSRequestError(
                        f"Groq TTS request failed with HTTP {response.status_code}: "
                        f"{response.text[:500]}"
                    )

                with open(out_path, "wb") as f:
                    f.write(response.content)

                if out_path.stat().st_size < 44:  # smaller than a minimal WAV header
                    raise TTSRequestError("Groq TTS returned an empty or invalid audio file.")

                return out_path

            except requests.Timeout as e:
                last_error = e
                backoff = BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
                logger.warning(
                    "Groq TTS request timed out (attempt %d/%d). Retrying in %.1fs.",
                    attempt, MAX_RETRIES, backoff,
                )
                time.sleep(backoff)
            except requests.RequestException as e:
                last_error = e
                backoff = BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
                logger.warning(
                    "Groq TTS network error (attempt %d/%d): %s. Retrying in %.1fs.",
                    attempt, MAX_RETRIES, e, backoff,
                )
                time.sleep(backoff)

        raise TTSRequestError(
            f"Groq TTS failed after {MAX_RETRIES} attempts for text starting "
            f"with '{text[:60]}...': {last_error}"
        )
