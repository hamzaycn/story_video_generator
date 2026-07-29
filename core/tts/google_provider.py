"""
Stub provider for non-English narration.

Groq's Orpheus English model cannot narrate other languages, so every
language other than English routes here. Wire this up to Google Cloud
Text-to-Speech (or Azure/ElevenLabs/etc.) once you have credentials --
it's kept behind the same `TTSProvider` interface so `video_builder.py`
and the CLI never need to know which vendor is actually narrating a scene.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Dict, Optional

from .base import TTSProvider, TTSRequestError

logger = logging.getLogger("story_video_generator")

# Minimal per-language default voice map -- extend as needed once wired up.
DEFAULT_VOICES: Dict[str, str] = {
    "es": "es-ES-Standard-A",
    "fr": "fr-FR-Standard-A",
    "de": "de-DE-Standard-A",
    "hi": "hi-IN-Standard-A",
    "ar": "ar-XA-Standard-A",
    "pt": "pt-BR-Standard-A",
    "ja": "ja-JP-Standard-A",
    "zh": "cmn-CN-Standard-A",
    "th": "th-TH-Standard-A",
    "he": "he-IL-Standard-A",
}


class GoogleCloudTTSProvider(TTSProvider):
    """
    TODO: implement real synthesis via the `google-cloud-texttospeech` SDK.

    Sketch of the real implementation once you have a service-account
    credential (`GOOGLE_APPLICATION_CREDENTIALS` env var pointing at the
    JSON key file):

        from google.cloud import texttospeech
        client = texttospeech.TextToSpeechClient()
        input_text = texttospeech.SynthesisInput(text=text)
        voice_params = texttospeech.VoiceSelectionParams(
            language_code=language, name=voice,
        )
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.LINEAR16,
            sample_rate_hertz=24000,
        )
        response = client.synthesize_speech(
            input=input_text, voice=voice_params, audio_config=audio_config,
        )
        out_path.write_bytes(response.audio_content)
        return out_path

    Until then this raises a clear, actionable error rather than silently
    producing no audio or mis-narrating with the wrong voice.
    """

    name = "google-cloud-tts"

    def __init__(self, credentials_path: Optional[str] = None):
        self.credentials_path = credentials_path or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")

    def supports_language(self, language: str) -> bool:
        prefix = language.split("-")[0].lower()
        # Explicitly "supports" (i.e. is the intended route for) anything
        # that isn't English -- actual synthesis still needs implementing.
        return prefix != "en"

    def synthesize(self, text: str, language: str, voice: str, *, out_path: Path) -> Path:
        raise TTSRequestError(
            "GoogleCloudTTSProvider is a stub -- no credentials/SDK call "
            "implemented yet. Implement synthesis using the "
            "google-cloud-texttospeech SDK (see class docstring for a "
            "ready-to-adapt sketch), set GOOGLE_APPLICATION_CREDENTIALS, and "
            "remove this exception. "
            f"(requested language='{language}', voice='{voice}', "
            f"credentials_path={self.credentials_path!r})"
        )
