"""
Caption rendering & SRT export -- the main quality bar for this project.

Design goals (see README for rationale):
  - Real text measurement via Pillow font metrics, never a character-count
    heuristic, so wrapping is correct at any font size/script.
  - Per-script font selection so Latin/Cyrillic/Greek, Arabic, Hebrew,
    Devanagari, CJK and Thai all render with real glyphs instead of tofu
    boxes.
  - Correct shaping + bidi reordering for RTL scripts (Arabic/Hebrew) via
    arabic_reshaper + python-bidi, with right-alignment.
  - Soft rounded-rect background bar, subtle drop shadow, comfortable
    bottom/top safe-area margins clear of TikTok/Reels/Shorts UI chrome.
  - Smooth fade in/out (no hard cuts).
  - Optional word-by-word ("karaoke") highlight, off by default since Groq
    gives us no word-level timestamps -- timing is approximated by
    character-length-weighted distribution across the known scene duration.
"""
from __future__ import annotations

import logging
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont

logger = logging.getLogger("story_video_generator")

RTL_LANGUAGE_PREFIXES = {"ar", "he", "fa", "ur"}

# BCP-47 primary subtag -> script "bucket". Extend as you add languages.
LANGUAGE_SCRIPT_MAP = {
    "ar": "arabic", "fa": "arabic", "ur": "arabic",
    "he": "hebrew",
    "hi": "devanagari", "mr": "devanagari", "ne": "devanagari",
    "zh": "cjk", "ja": "cjk", "ko": "cjk",
    "th": "thai",
    "ru": "cyrillic", "uk": "cyrillic", "bg": "cyrillic", "sr": "cyrillic",
    "el": "greek",
}
DEFAULT_SCRIPT = "latin"

# Default bundled-font filenames per script bucket. Ship these under
# assets/fonts/ -- all Noto families are OFL-licensed and safe to bundle.
# See README "Fonts" section for exact download links.
DEFAULT_SCRIPT_FONTS = {
    "latin": "NotoSans-Bold.ttf",
    "cyrillic": "NotoSans-Bold.ttf",
    "greek": "NotoSans-Bold.ttf",
    "arabic": "NotoNaskhArabic-Bold.ttf",
    "hebrew": "NotoSansHebrew-Bold.ttf",
    "devanagari": "NotoSansDevanagari-Bold.ttf",
    "cjk": "NotoSansSC-Bold.otf",
    "thai": "NotoSansThai-Bold.ttf",
}


def script_for_language(language: str) -> str:
    prefix = language.split("-")[0].lower()
    return LANGUAGE_SCRIPT_MAP.get(prefix, DEFAULT_SCRIPT)


def is_rtl(language: str) -> bool:
    return language.split("-")[0].lower() in RTL_LANGUAGE_PREFIXES


def resolve_font_path(language: str, fonts_dir: Path, font_map: Optional[dict] = None) -> Path:
    script = script_for_language(language)
    mapping = {**DEFAULT_SCRIPT_FONTS, **(font_map or {})}
    filename = mapping.get(script, DEFAULT_SCRIPT_FONTS[DEFAULT_SCRIPT])
    path = Path(fonts_dir) / filename
    if not path.exists():
        fallback = Path(fonts_dir) / DEFAULT_SCRIPT_FONTS[DEFAULT_SCRIPT]
        logger.warning(
            "Font '%s' for script '%s' (language=%s) not found in %s -- "
            "falling back to '%s'. Non-Latin glyphs may render as tofu boxes "
            "until you add the correct font file.",
            filename, script, language, fonts_dir, fallback.name,
        )
        path = fallback
    return path


def load_font(language: str, size: int, fonts_dir: Path,
               font_map: Optional[dict] = None) -> ImageFont.FreeTypeFont:
    path = resolve_font_path(language, fonts_dir, font_map)
    try:
        layout = getattr(ImageFont, "Layout", None)
        raqm_engine = layout.RAQM if layout is not None else ImageFont.LAYOUT_RAQM  # Pillow <10 vs >=10
        return ImageFont.truetype(str(path), size=size, layout_engine=raqm_engine)
    except Exception:
        # Either Pillow lacks libraqm support, or the Layout API differs.
        # Complex scripts (Arabic/Devanagari/Thai) will render with reduced
        # shaping quality (no ligatures/joining) but text still displays.
        logger.warning(
            "Raqm layout engine unavailable -- falling back to Pillow's "
            "basic layout engine. Install a libraqm-enabled Pillow build "
            "for correct shaping of Arabic/Devanagari/Thai/etc. (see README)."
        )
        return ImageFont.truetype(str(path), size=size)


def prepare_display_text(text: str, language: str) -> str:
    """
    Reshape + apply the bidi algorithm for RTL scripts so glyphs join
    correctly and reading order is visually right-to-left. No-op for LTR.
    """
    text = unicodedata.normalize("NFC", text)
    if not is_rtl(language):
        return text
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display

        reshaped = arabic_reshaper.reshape(text)
        return get_display(reshaped)
    except ImportError:
        logger.warning(
            "arabic_reshaper / python-bidi not installed -- RTL text will "
            "render unshaped and in logical (not visual) order. Run: "
            "pip install arabic-reshaper python-bidi"
        )
        return text


def measure(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> Tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int,
              draw: ImageDraw.ImageDraw) -> List[str]:
    """
    Greedy word-wrap using the font's *actual measured pixel width*, not a
    character-count heuristic (which breaks for proportional fonts, mixed
    Latin/CJK text, or any non-monospace script).

    Scripts without explicit word-separating spaces (CJK/Thai) fall back to
    wrapping per Unicode code point, which is an acceptable approximation
    without a full grapheme/word segmentation library.
    """
    if " " in text:
        words = text.split(" ")
        delim = " "
    else:
        words = list(text)
        delim = ""

    lines: List[str] = []
    current = ""
    for word in words:
        candidate = f"{current}{delim}{word}" if current else word
        w, _ = measure(draw, candidate, font)
        if w <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


@dataclass
class CaptionStyle:
    font_size: int = 58
    text_color: Tuple[int, int, int, int] = (255, 255, 255, 255)
    highlight_color: Tuple[int, int, int, int] = (255, 214, 64, 255)
    box_color: Tuple[int, int, int, int] = (0, 0, 0, 150)
    box_radius: int = 28
    box_padding_x: int = 40
    box_padding_y: int = 26
    shadow_color: Tuple[int, int, int, int] = (0, 0, 0, 160)
    shadow_offset: Tuple[int, int] = (0, 4)
    shadow_blur: int = 6
    max_width_ratio: float = 0.86        # fraction of canvas width text may use
    line_spacing: int = 14
    bottom_margin: int = 260             # stay clear of TikTok/Reels/Shorts UI chrome
    top_margin: int = 200
    fade_frames: int = 6                 # frames to fade caption in/out
    karaoke: bool = False


@dataclass
class WordTiming:
    word: str
    start: float
    end: float


def distribute_word_timings(text: str, duration: float) -> List[WordTiming]:
    """
    Approximate per-word start/end times by distributing the scene's known
    narration duration proportionally to each word's character length.
    This is a deliberate approximation (Groq TTS returns no word-level
    timestamps) -- adequate for a karaoke-style highlight sweep, not
    frame-accurate lip sync. Config-gated (`captions.karaoke`), off by
    default for exactly this reason.
    """
    words = [w for w in text.split(" ") if w]
    if not words:
        return []
    weights = [max(len(w), 1) for w in words]
    total_weight = sum(weights)
    timings: List[WordTiming] = []
    t = 0.0
    for w, weight in zip(words, weights):
        span = duration * (weight / total_weight)
        timings.append(WordTiming(word=w, start=t, end=t + span))
        t += span
    return timings


def render_caption_overlay(
    canvas_size: Tuple[int, int],
    text: str,
    language: str,
    font: ImageFont.FreeTypeFont,
    style: CaptionStyle,
    *,
    opacity: float = 1.0,
    active_word_index: Optional[int] = None,
) -> Image.Image:
    """
    Render one caption frame as an RGBA overlay the same size as the video
    canvas, ready to be alpha-composited onto a Ken Burns frame.
    """
    W, H = canvas_size
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    display_text = prepare_display_text(text, language)
    rtl = is_rtl(language)
    max_width = int(W * style.max_width_ratio)

    lines = wrap_text(display_text, font, max_width, draw)
    if not lines:
        return overlay

    line_sizes = [measure(draw, line, font) for line in lines]
    line_h = max((h for _, h in line_sizes), default=style.font_size) + style.line_spacing
    text_block_h = line_h * len(lines)
    block_w = max((w for w, _ in line_sizes), default=0)

    box_w = block_w + style.box_padding_x * 2
    box_h = text_block_h + style.box_padding_y * 2
    box_x0 = (W - box_w) / 2
    safe_bottom = H - style.bottom_margin
    box_y0 = max(safe_bottom - box_h, style.top_margin)

    alpha_mult = max(0.0, min(1.0, opacity))

    # --- drop shadow ---
    shadow_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow_layer)
    shadow_color = (*style.shadow_color[:3], int(style.shadow_color[3] * alpha_mult))
    sd.rounded_rectangle(
        [box_x0 + style.shadow_offset[0], box_y0 + style.shadow_offset[1],
         box_x0 + box_w + style.shadow_offset[0], box_y0 + box_h + style.shadow_offset[1]],
        radius=style.box_radius, fill=shadow_color,
    )
    if style.shadow_blur > 0:
        shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(style.shadow_blur))
    overlay = Image.alpha_composite(overlay, shadow_layer)
    draw = ImageDraw.Draw(overlay)

    # --- rounded background box ---
    box_color = (*style.box_color[:3], int(style.box_color[3] * alpha_mult))
    draw.rounded_rectangle([box_x0, box_y0, box_x0 + box_w, box_y0 + box_h],
                            radius=style.box_radius, fill=box_color)

    # --- text lines (optionally karaoke word-highlighted) ---
    words_seen = 0
    y = box_y0 + style.box_padding_y
    for line, (lw, lh) in zip(lines, line_sizes):
        x = (W - lw) / 2  # block is centered regardless of script direction
        if style.karaoke and active_word_index is not None and not rtl:
            words_seen = _draw_karaoke_line(
                draw, line, font, x, y, style, alpha_mult, active_word_index, words_seen,
            )
        else:
            color = (*style.text_color[:3], int(style.text_color[3] * alpha_mult))
            draw.text((x, y), line, font=font, fill=color)
        y += line_h

    return overlay


def _draw_karaoke_line(draw, line, font, x, y, style, alpha_mult,
                        active_word_index, words_seen) -> int:
    """Draw a line word-by-word, highlighting the currently 'spoken' word."""
    words = line.split(" ")
    cursor = x
    for i, w in enumerate(words):
        global_idx = words_seen + i
        is_active = global_idx == active_word_index
        base_color = style.highlight_color if is_active else style.text_color
        color = (*base_color[:3], int(base_color[3] * alpha_mult))
        piece = w if i == 0 else f" {w}"
        draw.text((cursor, y), piece, font=font, fill=color)
        cursor += draw.textlength(piece, font=font)
    return words_seen + len(words)


def format_srt_timestamp(seconds: float) -> str:
    ms_total = max(int(round(seconds * 1000)), 0)
    h, rem = divmod(ms_total, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(entries: Sequence[Tuple[float, float, str]], out_path: Path) -> Path:
    """entries: list of (start_seconds, end_seconds, text), in playback order."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines: List[str] = []
    for i, (start, end, text) in enumerate(entries, start=1):
        lines.append(str(i))
        lines.append(f"{format_srt_timestamp(start)} --> {format_srt_timestamp(end)}")
        lines.append(text.replace("\n", " "))
        lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path
