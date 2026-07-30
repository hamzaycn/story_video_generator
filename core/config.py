"""
AppConfig: typed representation of config.yaml.

Every visual/audio/timing tunable lives here so nothing downstream ever
hardcodes a magic number -- resolution, Ken Burns intensity, caption
styling, brand colors, and encode settings are all centralized and
overridable per-run via `--config`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional, Tuple

import yaml
from pydantic import BaseModel, Field


class ResolutionConfig(BaseModel):
    width: int = 1080
    height: int = 1920
    fps: int = 30


class KenBurnsConfigModel(BaseModel):
    zoom_min: float = 1.0
    zoom_max: float = 1.22


class CaptionConfigModel(BaseModel):
    font_size: int = 58
    text_color: Tuple[int, int, int, int] = (255, 255, 255, 255)
    highlight_color: Tuple[int, int, int, int] = (255, 214, 64, 255)
    box_color: Tuple[int, int, int, int] = (0, 0, 0, 150)
    box_radius: int = 28
    box_padding_x: int = 40
    box_padding_y: int = 26
    max_width_ratio: float = 0.86
    line_spacing: int = 14
    bottom_margin: int = 260
    top_margin: int = 200
    fade_frames: int = 6
    karaoke: bool = False


class FontsConfigModel(BaseModel):
    fonts_dir: str = "assets/fonts"
    script_map: Dict[str, str] = Field(default_factory=dict)


class BrandConfigModel(BaseModel):
    app_name: str = "StoryTime"
    primary_color: Tuple[int, int, int] = (91, 62, 224)
    secondary_color: Tuple[int, int, int] = (255, 176, 59)
    background_color: Tuple[int, int, int] = (18, 14, 38)
    text_color: Tuple[int, int, int] = (255, 255, 255)
    logo_path: Optional[str] = "assets/branding/logo_small.png"   # small watermark used on the intro card
    outro_card_path: Optional[str] = "assets/branding/logo.png"   # NEW — full pre-made download/QR card for the outro
    qr_code_path: Optional[str] = None
    cta_text: str = "Download the app to create your own stories!"
    title_font: Optional[str] = None
    intro_duration: float = 3.0
    outro_duration: float = 4.0
    narrate_intro: bool = True
    intro_narration_template: str = "{title}. A {category} story."


class AudioConfigModel(BaseModel):
    music_duck_db: float = -18.0
    music_default_volume_db: float = -12.0
    music_fade_out_s: float = 2.0
    narration_gain_db: float = 0.0


class OutputConfigModel(BaseModel):
    video_bitrate: str = "8M"
    audio_bitrate: str = "192k"
    crf: int = 18
    preset: str = "medium"


class TimingConfigModel(BaseModel):
    min_scene_duration_s: float = 3.0
    transition_duration_s: float = 0.5
    intro_lead_silence_s: float = 0.4     # NEW — beat of silence before narration begins
    outro_lead_pause_s: float = 0.6   


class PathsConfigModel(BaseModel):
    cache_dir: str = "cache"
    output_dir: str = "output"
    assets_dir: str = "assets"


class AppConfig(BaseModel):
    resolution: ResolutionConfig = Field(default_factory=ResolutionConfig)
    ken_burns: KenBurnsConfigModel = Field(default_factory=KenBurnsConfigModel)
    captions: CaptionConfigModel = Field(default_factory=CaptionConfigModel)
    fonts: FontsConfigModel = Field(default_factory=FontsConfigModel)
    brand: BrandConfigModel = Field(default_factory=BrandConfigModel)
    audio: AudioConfigModel = Field(default_factory=AudioConfigModel)
    output: OutputConfigModel = Field(default_factory=OutputConfigModel)
    timing: TimingConfigModel = Field(default_factory=TimingConfigModel)
    paths: PathsConfigModel = Field(default_factory=PathsConfigModel)
    default_voice: str = "hannah"


def load_config(path: Optional[Path]) -> AppConfig:
    """Load config.yaml if provided, else fall back to built-in defaults."""
    if path is None:
        return AppConfig()
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return AppConfig.model_validate(raw)
