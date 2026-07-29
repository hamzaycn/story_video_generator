"""
Story schema + loader.

Validates incoming story JSON (matching the app's existing data model)
against a strict pydantic schema before anything downstream (TTS, image
fetch, rendering) ever touches it. Fails fast with a clear message on
malformed data rather than surfacing a confusing ffmpeg/PIL error three
stages later.
"""
from __future__ import annotations

import json
import logging
from enum import Enum
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from urllib.parse import urlparse

logger = logging.getLogger("story_video_generator")


class ImageMode(str, Enum):
    SINGLE_COVER = "single_cover"
    PER_SCENE = "per_scene"


class Scene(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    scene_number: int = Field(..., alias="sceneNumber", ge=1)
    text: str = Field(..., min_length=1)
    image_url: Optional[str] = Field(default=None, alias="imageUrl")

    @field_validator("text")
    @classmethod
    def strip_text(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Scene text cannot be empty or whitespace-only")
        return v


class Story(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    title: str = Field(..., min_length=1)
    category: str = "general"
    language: str = Field(default="en-US")
    cover_image_url: Optional[str] = Field(default=None, alias="coverImageUrl")
    image_mode: ImageMode = Field(default=ImageMode.SINGLE_COVER, alias="imageMode")
    scenes: List[Scene]

    @model_validator(mode="after")
    def validate_story(self) -> "Story":
        if not self.scenes:
            raise ValueError("Story must contain at least one scene")

        numbers = [s.scene_number for s in self.scenes]
        if len(set(numbers)) != len(numbers):
            raise ValueError(f"Duplicate sceneNumber values found: {numbers}")

        if self.image_mode == ImageMode.SINGLE_COVER:
            if not self.cover_image_url:
                raise ValueError(
                    "imageMode='single_cover' requires a top-level coverImageUrl"
                )
        else:  # PER_SCENE
            missing = [s.scene_number for s in self.scenes if not s.image_url]
            if missing:
                raise ValueError(
                    f"imageMode='per_scene' requires imageUrl on every scene; "
                    f"missing for sceneNumber(s): {missing}"
                )
        return self

    def image_url_for_scene(self, scene: "Scene") -> str:
        """Resolve the correct image source for a scene given imageMode."""
        if self.image_mode == ImageMode.SINGLE_COVER:
            return self.cover_image_url  # type: ignore[return-value]
        return scene.image_url  # type: ignore[return-value]

    @property
    def sorted_scenes(self) -> List[Scene]:
        return sorted(self.scenes, key=lambda s: s.scene_number)


def load_story(path: Path) -> Story:
    """Load and validate a story JSON file, raising a descriptive error on failure."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Story file not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"Story file '{path}' is not valid JSON: {e}") from e

    story = Story.model_validate(data)

    logger.info(
        "Loaded story '%s' (id=%s): %d scene(s), language=%s, imageMode=%s",
        story.title, story.id, len(story.scenes), story.language, story.image_mode.value,
    )
    return story


def resolve_local_paths(story: Story, base_dir: Path) -> Story:
    """
    story.json ships with relative local paths like 'assets/images/cover.png'
    meant relative to the *story folder*, not the process cwd. Rewrite any
    non-URL image path to an absolute path before anything downstream
    (image_manager) touches it.
    """
    base_dir = Path(base_dir).resolve()

    def resolve(src: Optional[str]) -> Optional[str]:
        if not src:
            return src
        if urlparse(src).scheme in ("http", "https"):
            return src  # remote URL, leave as-is
        p = Path(src)
        return str(p if p.is_absolute() else (base_dir / p).resolve())

    story.cover_image_url = resolve(story.cover_image_url)
    for scene in story.scenes:
        scene.image_url = resolve(scene.image_url)
    return story
