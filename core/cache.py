"""
Content-addressed caching for downloaded images and generated narration.

Never re-download an unchanged image or re-synthesize unchanged narration
on reruns -- both cost real money/time (API calls) or bandwidth.
"""
from __future__ import annotations

import hashlib
from pathlib import Path


class CacheManager:
    """
    Owns the on-disk cache layout:

        cache/
          images/<hash>.<ext>
          audio/<hash>.wav
          frames/                (scratch space some callers may use)
    """

    def __init__(self, cache_dir: Path):
        self.cache_dir = Path(cache_dir)
        self.image_dir = self.cache_dir / "images"
        self.audio_dir = self.cache_dir / "audio"
        self.frames_dir = self.cache_dir / "frames"
        for d in (self.image_dir, self.audio_dir, self.frames_dir):
            d.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def hash_key(*parts: str) -> str:
        h = hashlib.sha256()
        for p in parts:
            h.update(str(p).encode("utf-8"))
            h.update(b"\x00")
        return h.hexdigest()[:24]

    def image_cache_path(self, url_or_path: str, suffix: str = ".jpg") -> Path:
        """Cache key is the URL/path itself -- unchanged source -> cache hit."""
        key = self.hash_key("image", url_or_path)
        if not suffix.startswith("."):
            suffix = f".{suffix}"
        return self.image_dir / f"{key}{suffix}"

    def audio_cache_path(self, text: str, voice: str, model: str, language: str) -> Path:
        """
        Cache key includes text + voice + model + language: any of these
        changing invalidates the cache and forces a fresh TTS call, which is
        exactly the behavior we want (e.g. editing scene text or switching
        voices should regenerate narration; nothing else should).
        """
        key = self.hash_key("audio", text, voice, model, language)
        return self.audio_dir / f"{key}.wav"

    def is_cached(self, path: Path) -> bool:
        return Path(path).exists() and Path(path).stat().st_size > 0
