"""
Kokoro TTS provider — runs fully locally (no API key, no network call, no
per-character cost, no rate limits). Ideal for Colab batch rendering.
Requires: pip install kokoro soundfile numpy
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import soundfile as sf

from .base import TTSProvider, TTSRequestError

logger = logging.getLogger("story_video_generator")

# BCP-47 primary subtag -> Kokoro's own lang_code. Extend as Kokoro adds
# languages/voices (see https://github.com/hexgrad/kokoro for the current
# list — at time of writing: a=American English, b=British English,
# j=Japanese, z=Mandarin, e=Spanish, f=French, h=Hindi, i=Italian,
# p=Brazilian Portuguese).
KOKORO_LANG_MAP = {
    "en": "a",
}

DEFAULT_SAMPLE_RATE = 24000


class KokoroTTSProvider(TTSProvider):
    name = "kokoro"

    def __init__(self, lang_code: Optional[str] = None, sample_rate: int = DEFAULT_SAMPLE_RATE):
        self._lang_code_override = lang_code
        self.sample_rate = sample_rate
        self._pipelines: Dict[str, "object"] = {}  # lazily built, reused across calls

    def supports_language(self, language: str) -> bool:
        prefix = language.split("-")[0].lower()
        return (self._lang_code_override is not None) or (prefix in KOKORO_LANG_MAP)

    def _get_pipeline(self, lang_code: str):
        from kokoro import KPipeline  # heavy import (pulls in torch) -- deferred until actually needed

        if lang_code not in self._pipelines:
            logger.info("Initializing Kokoro pipeline (lang_code=%s)", lang_code)
            self._pipelines[lang_code] = KPipeline(lang_code=lang_code)
        return self._pipelines[lang_code]

    def synthesize(self, text: str, language: str, voice: str, *, out_path: Path) -> Path:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        prefix = language.split("-")[0].lower()
        lang_code = self._lang_code_override or KOKORO_LANG_MAP.get(prefix)
        if lang_code is None:
            raise TTSRequestError(
                f"Kokoro has no lang_code mapping for language '{language}'. "
                f"Add an entry to KOKORO_LANG_MAP in kokoro_provider.py."
            )

        try:
            pipeline = self._get_pipeline(lang_code)
            generator = pipeline(text, voice=voice, speed=0.8, split_pattern=r"\n+")

            chunks = [audio for _, _, audio in generator]
            if not chunks:
                raise TTSRequestError(f"Kokoro produced no audio for text: {text[:80]!r}")
            full_audio = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]

            # Atomic write: synthesize to a temp file, then rename, so a
            # killed process never leaves a corrupt/partial file that
            # cache.is_cached() would mistake for a valid cache hit.
            # format="WAV" is explicit -- don't rely on soundfile guessing
            # from the temp filename's extension.
            tmp_path = out_path.with_name(out_path.name + ".part")
            sf.write(str(tmp_path), full_audio, self.sample_rate, format="WAV")
            tmp_path.replace(out_path)
            return out_path

        except TTSRequestError:
            raise
        except Exception as e:
            raise TTSRequestError(f"Kokoro synthesis failed: {e}") from e