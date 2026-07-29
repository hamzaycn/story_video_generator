"""
Abstract interface for text-to-speech providers.

Any concrete provider (Groq, Google Cloud, ElevenLabs, Azure, etc.) must
implement `TTSProvider`. This keeps `video_builder.py` and the CLI fully
decoupled from any single vendor's API, and makes it trivial to add new
languages/backends without touching the rendering pipeline.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class TTSError(Exception):
    """Base class for all TTS-related failures."""


class TTSRequestError(TTSError):
    """Raised when the TTS API call fails after retries (network, 4xx/5xx, auth, etc.)."""


class TTSRateLimitError(TTSError):
    """Raised when the provider rate-limits us and retries are exhausted."""


class UnsupportedLanguageError(TTSError):
    """
    Raised when no TTS provider is configured/available for a story's
    language. This is intentionally loud -- silently mis-narrating a
    children's story (e.g. reading Arabic text through an English-only
    voice model) is worse than failing the run outright.
    """


class TTSProvider(ABC):
    """Common interface every narration backend must implement."""

    #: Human readable identifier, used in logs and cache keys.
    name: str = "base"

    @abstractmethod
    def supports_language(self, language: str) -> bool:
        """Return True if this provider can narrate the given BCP-47 language tag."""
        raise NotImplementedError

    @abstractmethod
    def synthesize(self, text: str, language: str, voice: str, *, out_path: Path) -> Path:
        """
        Synthesize `text` into speech and write a WAV file to `out_path`.

        Implementations must:
          - Create parent directories for `out_path` if needed.
          - Write a valid, playable WAV file (mono or stereo, any sample
            rate `soundfile` can read) at exactly `out_path`.
          - Raise `TTSRequestError` / `TTSRateLimitError` on failure instead
            of returning a partial/corrupt file, so callers never silently
            proceed with broken narration.

        Returns `out_path` for convenient chaining.
        """
        raise NotImplementedError
