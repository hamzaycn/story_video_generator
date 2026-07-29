"""
Unit tests for pure logic (no ffmpeg/network/PIL-file I/O required).

Run with:  pytest tests/
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.ken_burns import (  # noqa: E402
    ease_in_out_cubic,
    compute_crop_box,
    required_upscale_size,
    variant_for_scene,
    KenBurnsConfig,
    PanDirection,
)
from core.subtitles import (  # noqa: E402
    distribute_word_timings,
    format_srt_timestamp,
    script_for_language,
    is_rtl,
)


def test_easing_bounds():
    assert ease_in_out_cubic(0.0) == 0.0
    assert ease_in_out_cubic(1.0) == 1.0
    assert 0.0 < ease_in_out_cubic(0.5) < 1.0


def test_easing_is_monotonic():
    xs = [i / 100 for i in range(101)]
    ys = [ease_in_out_cubic(x) for x in xs]
    assert all(y2 >= y1 for y1, y2 in zip(ys, ys[1:]))


def test_crop_box_shrinks_as_zoom_increases():
    cfg = KenBurnsConfig(zoom_start=1.0, zoom_end=1.5, pan=PanDirection.CENTER)
    box_start = compute_crop_box(0.0, 2000, 3000, 1080 / 1920, cfg)
    box_end = compute_crop_box(1.0, 2000, 3000, 1080 / 1920, cfg)
    w_start = box_start[2] - box_start[0]
    w_end = box_end[2] - box_end[0]
    assert w_end < w_start  # more zoom -> smaller crop window


def test_crop_box_never_exceeds_source_bounds():
    cfg = KenBurnsConfig(zoom_start=1.0, zoom_end=1.3, pan=PanDirection.LEFT_TO_RIGHT)
    for t in (0.0, 0.25, 0.5, 0.75, 1.0):
        x0, y0, x1, y1 = compute_crop_box(t, 1600, 900, 1080 / 1920, cfg)
        assert x0 >= -1e-6 and y0 >= -1e-6
        assert x1 <= 1600 + 1e-6 and y1 <= 900 + 1e-6


def test_variant_for_scene_alternates_zoom_direction():
    v0 = variant_for_scene(0)
    v1 = variant_for_scene(1)
    assert v0.zoom_start < v0.zoom_end   # zoom in
    assert v1.zoom_start > v1.zoom_end   # zoom out


def test_variant_for_scene_rotates_pan_direction():
    pans = {variant_for_scene(i).pan for i in range(6)}
    assert len(pans) == 6  # all 6 rotation slots are distinct


def test_required_upscale_size_never_shrinks_below_target():
    w, h = required_upscale_size(200, 100, target_w=1080, target_h=1920, max_zoom=1.3)
    assert w >= 1080 and h >= 1920


def test_word_timing_distribution_sums_to_duration():
    text = "one two three four"
    timings = distribute_word_timings(text, 8.0)
    assert len(timings) == 4
    assert abs(timings[-1].end - 8.0) < 1e-6
    # a longer word should get a longer (or equal) time slice than a shorter one
    assert (timings[2].end - timings[2].start) >= (timings[0].end - timings[0].start)


def test_srt_timestamp_format():
    assert format_srt_timestamp(0) == "00:00:00,000"
    assert format_srt_timestamp(65.25) == "00:01:05,250"
    assert format_srt_timestamp(3661.5) == "01:01:01,500"


def test_script_and_rtl_detection():
    assert script_for_language("en-US") == "latin"
    assert script_for_language("ar-SA") == "arabic"
    assert script_for_language("hi-IN") == "devanagari"
    assert is_rtl("ar-SA") is True
    assert is_rtl("en-US") is False
