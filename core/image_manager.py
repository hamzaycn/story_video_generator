"""
Image acquisition: download (with caching) or load local files, with a
graceful branded-placeholder fallback so one broken URL never aborts an
entire render.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urlparse

import requests
from PIL import Image, ImageDraw, ImageFont

from .cache import CacheManager

logger = logging.getLogger("story_video_generator")

REQUEST_TIMEOUT_S = 20
MAX_RETRIES = 3


def _is_url(source: str) -> bool:
    try:
        return urlparse(source).scheme in ("http", "https")
    except Exception:
        return False


def _download(url: str, dest: Path) -> Path:
    last_err: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, timeout=REQUEST_TIMEOUT_S, stream=True)
            resp.raise_for_status()
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp_dest = dest.with_suffix(dest.suffix + ".part")
            with open(tmp_dest, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1 << 16):
                    f.write(chunk)
            tmp_dest.replace(dest)
            return dest
        except Exception as e:  # noqa: BLE001 - want to retry on anything network related
            last_err = e
            logger.warning(
                "Image download attempt %d/%d failed for %s: %s",
                attempt, MAX_RETRIES, url, e,
            )
    raise RuntimeError(f"Failed to download image after {MAX_RETRIES} attempts: {url}") from last_err


def make_placeholder(
    width: int, height: int, brand_color: Tuple[int, int, int], label: str,
    fonts_dir: Optional[Path] = None,
) -> Image.Image:
    """Branded solid-color placeholder used when a scene image can't be fetched."""
    img = Image.new("RGB", (width, height), brand_color)
    draw = ImageDraw.Draw(img)
    text = f"Image unavailable\n{label}".strip()

    font = None
    if fonts_dir:
        candidate = Path(fonts_dir) / "NotoSans-Bold.ttf"
        if candidate.exists():
            try:
                font = ImageFont.truetype(str(candidate), 48)
            except Exception:
                font = None
    if font is None:
        font = ImageFont.load_default()

    bbox = draw.multiline_textbbox((0, 0), text, font=font, align="center")
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.multiline_text(
        ((width - tw) / 2, (height - th) / 2), text,
        font=font, fill=(255, 255, 255), align="center",
    )
    return img


def get_image(
    source: str,
    cache: CacheManager,
    *,
    brand_color: Tuple[int, int, int] = (60, 60, 90),
    label: str = "",
    fonts_dir: Optional[Path] = None,
    placeholder_size: Tuple[int, int] = (1080, 1920),
) -> Image.Image:
    """
    Resolve `source` (a remote URL or local file path) to a PIL Image.

    Remote URLs are cached to disk by hash so unchanged assets are never
    re-downloaded on reruns. Any failure (bad URL, 404, corrupt file,
    missing local path) is logged as a warning and swapped for a branded
    placeholder -- this function never raises, so a single bad asset never
    aborts a full multi-scene render.
    """
    try:
        if _is_url(source):
            suffix = Path(urlparse(source).path).suffix or ".jpg"
            cached_path = cache.image_cache_path(source, suffix=suffix)
            if not cache.is_cached(cached_path):
                logger.info("Downloading image: %s", source)
                _download(source, cached_path)
            else:
                logger.debug("Using cached image for %s", source)
            return Image.open(cached_path).convert("RGB")
        else:
            path = Path(source)
            if not path.exists():
                raise FileNotFoundError(f"Local image not found: {path}")
            return Image.open(path).convert("RGB")
    except Exception as e:  # noqa: BLE001 - graceful degradation is the point
        logger.warning("Could not load image '%s' (%s). Using placeholder.", source, e)
        return make_placeholder(
            placeholder_size[0], placeholder_size[1], brand_color, label, fonts_dir
        )


