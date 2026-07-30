"""
Language -> TTSProvider resolution.

`story.language` (BCP-47, e.g. "en-US", "ar-SA", "hi-IN") drives provider
selection so a story is never silently narrated in the wrong language by
the wrong model. Extend `get_tts_provider` as you wire in more backends.
"""
from __future__ import annotations

import logging
from typing import Dict

from .base import TTSProvider, UnsupportedLanguageError
from .google_provider import GoogleCloudTTSProvider
# from .groq_provider import GroqTTSProvider
from .kokoro_provider import KokoroTTSProvider

logger = logging.getLogger("story_video_generator")

_provider_cache: Dict[str, TTSProvider] = {}


def get_tts_provider(language: str) -> TTSProvider:
    """
    Return a ready-to-use TTSProvider for `language`.

    Raises `UnsupportedLanguageError` with an actionable message instead of
    silently falling back to the wrong language/model -- mis-narrating a
    children's story is worse than a loud, early failure.
    """
    prefix = language.split("-")[0].lower()

    if prefix == "en":
        return KokoroTTSProvider()

    # Everything else routes to the (stub) Google Cloud provider for now.
    key = "google-cloud"
    if key not in _provider_cache:
        _provider_cache[key] = GoogleCloudTTSProvider()
    provider = _provider_cache[key]

    if not provider.supports_language(language):
        raise UnsupportedLanguageError(
            f"No TTS provider is configured for language '{language}'. "
            f"Add/enable a provider in core/tts/ and register it in "
            f"core/tts/factory.py before rendering this story."
        )
    return provider
