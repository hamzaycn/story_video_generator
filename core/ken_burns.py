"""
Ken Burns pan/zoom math + frame rendering.

The math in this module (`ease_in_out_cubic`, `variant_for_scene`,
`compute_crop_box`, `required_upscale_size`) is intentionally pure -- no
Pillow/ffmpeg I/O -- so it is fully unit-testable in isolation. Frames are
generated directly via Pillow crop+resize per output frame (not ffmpeg's
`zoompan`, which visibly jitters at low frame counts / long durations).
`KenBurnsAnimator` wraps the math with actual image I/O for rendering.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterator, Tuple

from PIL import Image

TARGET_W = 1080
TARGET_H = 1920


class PanDirection(str, Enum):
    LEFT_TO_RIGHT = "left_to_right"
    RIGHT_TO_LEFT = "right_to_left"
    TOP_TO_BOTTOM = "top_to_bottom"
    BOTTOM_TO_TOP = "bottom_to_top"
    DIAGONAL_TL_BR = "diagonal_tl_br"
    DIAGONAL_BR_TL = "diagonal_br_tl"
    CENTER = "center"


@dataclass(frozen=True)
class KenBurnsConfig:
    zoom_start: float          # e.g. 1.0  (1.0 == no crop, full frame)
    zoom_end: float            # e.g. 1.22
    pan: PanDirection
    easing: str = "ease_in_out_cubic"


def ease_in_out_cubic(t: float) -> float:
    """Smooth accelerate-then-decelerate curve. t in [0, 1] -> [0, 1]."""
    t = min(max(t, 0.0), 1.0)
    if t < 0.5:
        return 4 * t * t * t
    p = -2 * t + 2
    return 1 - (p ** 3) / 2


EASINGS = {
    "linear": lambda t: min(max(t, 0.0), 1.0),
    "ease_in_out_cubic": ease_in_out_cubic,
}

# Alternate zoom direction + pan direction per scene index so a multi-scene
# story never feels like the same animation looping.
_PAN_ROTATION = [
    PanDirection.LEFT_TO_RIGHT,
    PanDirection.TOP_TO_BOTTOM,
    PanDirection.DIAGONAL_TL_BR,
    PanDirection.RIGHT_TO_LEFT,
    PanDirection.BOTTOM_TO_TOP,
    PanDirection.DIAGONAL_BR_TL,
]


def variant_for_scene(scene_index: int, zoom_min: float = 1.0, zoom_max: float = 1.22) -> KenBurnsConfig:
    """
    Deterministically pick an alternating zoom-in/zoom-out + rotating pan
    direction for a given (0-indexed) scene. Deterministic on purpose --
    reruns of the same story produce the same animation, which matters for
    caching/regression comparisons.
    """
    pan = _PAN_ROTATION[scene_index % len(_PAN_ROTATION)]
    zoom_in = (scene_index % 2 == 0)
    if zoom_in:
        return KenBurnsConfig(zoom_start=zoom_min, zoom_end=zoom_max, pan=pan)
    return KenBurnsConfig(zoom_start=zoom_max, zoom_end=zoom_min, pan=pan)


def compute_crop_box(
    t: float,
    src_w: int,
    src_h: int,
    target_aspect: float,
    config: KenBurnsConfig,
) -> Tuple[float, float, float, float]:
    """
    Pure function: given normalized time `t` in [0, 1], source dimensions,
    the desired output aspect ratio, and a KenBurnsConfig, return the crop
    box (x0, y0, x1, y1) in source pixel coordinates.

    "Zoom" is simulated by shrinking the cropped region (the caller then
    scales the crop up to fill the output frame). "Pan" moves that crop
    window across the available travel range as `t` progresses.
    """
    easing = EASINGS.get(config.easing, ease_in_out_cubic)
    eased_t = easing(t)
    zoom = config.zoom_start + (config.zoom_end - config.zoom_start) * eased_t
    zoom = max(zoom, 1.0)

    # Base crop (zoom == 1) covers the full source, matching target aspect.
    src_aspect = src_w / src_h
    if src_aspect > target_aspect:
        base_h = float(src_h)
        base_w = base_h * target_aspect
    else:
        base_w = float(src_w)
        base_h = base_w / target_aspect

    crop_w = base_w / zoom
    crop_h = base_h / zoom

    max_x = max(src_w - crop_w, 0.0)
    max_y = max(src_h - crop_h, 0.0)

    pan = config.pan
    if pan == PanDirection.LEFT_TO_RIGHT:
        x0, y0 = max_x * eased_t, max_y * 0.5
    elif pan == PanDirection.RIGHT_TO_LEFT:
        x0, y0 = max_x * (1 - eased_t), max_y * 0.5
    elif pan == PanDirection.TOP_TO_BOTTOM:
        x0, y0 = max_x * 0.5, max_y * eased_t
    elif pan == PanDirection.BOTTOM_TO_TOP:
        x0, y0 = max_x * 0.5, max_y * (1 - eased_t)
    elif pan == PanDirection.DIAGONAL_TL_BR:
        x0, y0 = max_x * eased_t, max_y * eased_t
    elif pan == PanDirection.DIAGONAL_BR_TL:
        x0, y0 = max_x * (1 - eased_t), max_y * (1 - eased_t)
    else:  # CENTER
        x0, y0 = max_x * 0.5, max_y * 0.5

    return x0, y0, x0 + crop_w, y0 + crop_h


def required_upscale_size(
    src_w: int, src_h: int, target_w: int = TARGET_W, target_h: int = TARGET_H,
    max_zoom: float = 1.3,
) -> Tuple[int, int]:
    """
    Compute a size to pre-upscale the source image to, so that even at
    maximum zoom the cropped region is still >= target resolution
    (otherwise the final upscale-to-fill-frame step visibly pixelates).
    Never proposes downsampling below the image's native resolution.
    """
    target_aspect = target_w / target_h
    src_aspect = src_w / src_h
    if src_aspect > target_aspect:
        base_h, base_w = float(src_h), src_h * target_aspect
    else:
        base_w, base_h = float(src_w), src_w / target_aspect

    min_crop_h = base_h / max(max_zoom, 1.0)
    scale_needed = (target_h / min_crop_h) if min_crop_h > 0 else 1.0
    scale_needed = max(scale_needed, 1.0)

    new_w = max(int(round(src_w * scale_needed)), target_w)
    new_h = max(int(round(src_h * scale_needed)), target_h)
    return new_w, new_h


class KenBurnsAnimator:
    """Handles the actual image I/O: upscaling once, then per-frame crop+resize."""

    def __init__(self, image: Image.Image, config: KenBurnsConfig,
                 target_w: int = TARGET_W, target_h: int = TARGET_H):
        self.config = config
        self.target_w = target_w
        self.target_h = target_h

        image = image.convert("RGB")
        up_w, up_h = required_upscale_size(
            image.width, image.height, target_w, target_h,
            max(config.zoom_start, config.zoom_end),
        )
        if (up_w, up_h) != image.size:
            image = image.resize((up_w, up_h), Image.LANCZOS)
        self.image = image

    def frame_at(self, t: float) -> Image.Image:
        """t in [0, 1] -> a target_w x target_h RGB frame."""
        box = compute_crop_box(
            t, self.image.width, self.image.height,
            self.target_w / self.target_h, self.config,
        )
        crop = self.image.crop(tuple(round(v) for v in box))
        return crop.resize((self.target_w, self.target_h), Image.LANCZOS)

    def iter_frames(self, duration_s: float, fps: int) -> Iterator[Image.Image]:
        n = max(int(round(duration_s * fps)), 1)
        for i in range(n):
            t = i / max(n - 1, 1)
            yield self.frame_at(t)
