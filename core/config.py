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
    app_name: str = "Kids AI Stories"
    primary_color: Tuple[int, int, int] = (91, 62, 224)
    secondary_color: Tuple[int, int, int] = (255, 176, 59)
    background_color: Tuple[int, int, int] = (18, 14, 38)
    text_color: Tuple[int, int, int] = (255, 255, 255)
    logo_path: Optional[str] = "assets/branding/logo.png"
    qr_code_path: Optional[str] = None
    cta_text: str = "Download the app to create your own stories!"
    title_font: Optional[str] = None  # falls back to per-script default if unset
    intro_duration: float = 3.0
    outro_duration: float = 4.0


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

# Add to config.py:

def load_config(path: Optional[Path], preset: Optional[str] = None) -> AppConfig:
    """Load config.yaml, optionally applying a preset first."""
    base_config = {}
    
    # Load base config
    if path and path.exists():
        with path.open("r", encoding="utf-8") as f:
            base_config = yaml.safe_load(f) or {}
    
    # Apply preset if specified
    if preset:
        presets_path = Path(__file__).parent.parent / "presets.yaml"
        if presets_path.exists():
            with presets_path.open("r", encoding="utf-8") as f:
                presets = yaml.safe_load(f) or {}
                if preset in presets:
                    # Deep merge preset into base config
                    base_config = _deep_merge(base_config, presets[preset])
                    logger.info(f"Applied preset: {preset}")
                else:
                    logger.warning(f"Preset '{preset}' not found in {presets_path}")
    
    return AppConfig.model_validate(base_config)


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep merge override into base."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result
