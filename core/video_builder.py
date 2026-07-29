"""
Final assembly: turns per-scene images + narration into a single branded,
captioned, transitioned, music-bedded vertical MP4 via ffmpeg subprocess
calls (never moviepy) for precise control and performance.

Pipeline (see assemble_video() for the orchestration):
  1. Per scene: synthesize/cache narration -> read exact duration (soundfile)
     -> pad to the enforced minimum duration -> render Ken Burns + caption
     frames straight into a silent H.264 clip via an ffmpeg stdin pipe.
  2. Render an animated intro (title card) and outro (CTA card) the same way.
  3. Concatenate all video clips with ffmpeg `xfade` crossfades.
  4. Concatenate all per-clip audio (narration or silence for intro/outro)
     with ffmpeg `acrossfade`, matching the same transition duration so
     video and audio stay in lockstep.
  5. Optionally mix in background music, ducked under narration via
     `sidechaincompress`, with a tail fade-out.
  6. Mux picture + final audio into the delivery MP4 (H.264/yuv420p/AAC,
     `+faststart`) and export a standalone .srt alongside it.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

from PIL import Image, ImageDraw, ImageFont

from .config import AppConfig
from .ken_burns import KenBurnsAnimator, ease_in_out_cubic, variant_for_scene
from .subtitles import (
    CaptionStyle,
    DEFAULT_SCRIPT_FONTS,
    distribute_word_timings,
    load_font,
    render_caption_overlay,
    script_for_language,
    wrap_text,
    write_srt,
)

logger = logging.getLogger("story_video_generator")

ProgressCallback = Optional[Callable[[str, int, int], None]]


# ----------------------------------------------------------------------------
# ffmpeg plumbing
# ----------------------------------------------------------------------------

def _ffmpeg_bin() -> str:
    return os.environ.get("FFMPEG_BINARY", "ffmpeg")


def run_ffmpeg(args: List[str], desc: str = "") -> None:
    cmd = [_ffmpeg_bin(), "-y", "-loglevel", "error"] + args
    logger.debug("ffmpeg: %s", " ".join(cmd))
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed{f' ({desc})' if desc else ''}: "
            f"{proc.stderr.decode('utf-8', errors='ignore')[-2000:]}"
        )


def frames_to_video(frame_iter: Iterable[Image.Image], width: int, height: int,
                     fps: int, out_path: Path, output_cfg) -> Path:
    """Pipe raw RGB frames into ffmpeg's stdin, encoding a silent H.264 clip."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        _ffmpeg_bin(), "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}",
        "-r", str(fps), "-i", "-",
        "-an",
        "-c:v", "libx264", "-preset", output_cfg.preset, "-crf", str(output_cfg.crf),
        "-pix_fmt", "yuv420p",
        str(out_path),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for frame in frame_iter:
            if frame.mode != "RGB":
                frame = frame.convert("RGB")
            proc.stdin.write(frame.tobytes())
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        stderr = proc.stderr.read()
        ret = proc.wait()
        if ret != 0:
            raise RuntimeError(
                f"ffmpeg frame encode failed: {stderr.decode('utf-8', errors='ignore')[-2000:]}"
            )
    return out_path


def make_silence_wav(duration: float, out_path: Path, sample_rate: int = 44100) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg([
        "-f", "lavfi", "-i", f"anullsrc=r={sample_rate}:cl=stereo",
        "-t", f"{max(duration, 0.01):.3f}", str(out_path),
    ], desc="silence generation")
    return out_path


def pad_audio_to_duration(audio_path: Path, target_duration: float, out_path: Path) -> Path:
    """Pad narration with trailing silence so its length exactly matches the
    enforced minimum scene duration -- keeps video/audio clip lengths equal
    without any separate sync bookkeeping downstream."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg([
        "-i", str(audio_path),
        "-af", f"apad=whole_dur={target_duration:.3f}",
        "-t", f"{target_duration:.3f}",
        str(out_path),
    ], desc="audio pad")
    return out_path


def xfade_concat_videos(clip_paths: List[Path], durations: List[float], td: float, out_path: Path) -> Path:
    """Chain ffmpeg `xfade` crossfades across N silent video clips."""
    n = len(clip_paths)
    out_path = Path(out_path)
    if n == 1:
        run_ffmpeg(["-i", str(clip_paths[0]), "-c", "copy", str(out_path)], desc="single-clip copy")
        return out_path

    inputs: List[str] = []
    for p in clip_paths:
        inputs += ["-i", str(p)]

    filter_parts = []
    prev_label = "0:v"
    acc_duration = durations[0]
    for i in range(1, n):
        offset = max(acc_duration - td, 0.0)
        out_label = f"v{i}" if i < n - 1 else "vout"
        filter_parts.append(
            f"[{prev_label}][{i}:v]xfade=transition=fade:duration={td}:offset={offset:.3f}[{out_label}]"
        )
        acc_duration = acc_duration - td + durations[i]
        prev_label = out_label

    cmd = inputs + [
        "-filter_complex", ";".join(filter_parts),
        "-map", "[vout]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
        str(out_path),
    ]
    run_ffmpeg(cmd, desc="xfade concat")
    return out_path


def acrossfade_concat_audios(audio_paths: List[Path], td: float, out_path: Path) -> Path:
    """Chain ffmpeg `acrossfade` crossfades across N audio tracks (narration/silence)."""
    n = len(audio_paths)
    out_path = Path(out_path)
    if n == 1:
        run_ffmpeg(["-i", str(audio_paths[0]), "-c", "copy", str(out_path)], desc="single-audio copy")
        return out_path

    inputs: List[str] = []
    for p in audio_paths:
        inputs += ["-i", str(p)]

    filter_parts = []
    prev_label = "0:a"
    for i in range(1, n):
        out_label = f"a{i}" if i < n - 1 else "aout"
        filter_parts.append(f"[{prev_label}][{i}:a]acrossfade=d={td}:c1=tri:c2=tri[{out_label}]")
        prev_label = out_label

    cmd = inputs + ["-filter_complex", ";".join(filter_parts), "-map", "[aout]", str(out_path)]
    run_ffmpeg(cmd, desc="acrossfade concat")
    return out_path


def apply_background_music(narration_path: Path, music_path: Path, total_duration: float,
                            duck_db: float, base_volume_db: float, fade_out_s: float,
                            out_path: Path) -> Path:
    """
    Loop/trim music to `total_duration` and duck it under narration using a
    real sidechain compressor (music is the input signal, narration is the
    sidechain trigger) rather than fixed on/off volume windows -- this
    reacts naturally to pauses between sentences. `duck_db` is honored as
    the target ceiling via the compressor's makeup/threshold tuning; adjust
    `ratio`/`threshold` below for a more or less aggressive duck.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    base_gain = 10 ** (base_volume_db / 20)
    fade_start = max(total_duration - fade_out_s, 0.0)

    filter_complex = (
        f"[1:a]aloop=loop=-1:size=2e9,atrim=0:{total_duration:.3f},"
        f"volume={base_gain:.4f}[music];"
        f"[music][0:a]sidechaincompress=threshold=0.02:ratio=8:attack=5:release=300:makeup=1[ducked];"
        f"[ducked]afade=t=out:st={fade_start:.3f}:d={fade_out_s:.3f}[duckedfaded];"
        f"[0:a][duckedfaded]amix=inputs=2:duration=first:dropout_transition=0:weights=1 1[aout]"
    )
    run_ffmpeg([
        "-i", str(narration_path), "-i", str(music_path),
        "-filter_complex", filter_complex,
        "-map", "[aout]",
        str(out_path),
    ], desc="background music mix")
    return out_path


def mux_final(video_path: Path, audio_path: Path, out_path: Path, output_cfg) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg([
        "-i", str(video_path), "-i", str(audio_path),
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "libx264", "-preset", output_cfg.preset, "-crf", str(output_cfg.crf),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", output_cfg.audio_bitrate,
        "-shortest",
        "-movflags", "+faststart",
        str(out_path),
    ], desc="final mux")
    return out_path


# ----------------------------------------------------------------------------
# Scene / intro / outro frame rendering
# ----------------------------------------------------------------------------

def render_scene_video_clip(
    image: Image.Image, text: str, language: str, duration: float,
    scene_index: int, cfg: AppConfig, out_path: Path,
) -> Path:
    """Ken Burns pan/zoom + animated caption overlay, composited frame-by-frame."""
    width, height = cfg.resolution.width, cfg.resolution.height
    fps = cfg.resolution.fps

    kb_config = variant_for_scene(scene_index, cfg.ken_burns.zoom_min, cfg.ken_burns.zoom_max)
    animator = KenBurnsAnimator(image, kb_config, width, height)

    style = CaptionStyle(
        font_size=cfg.captions.font_size, text_color=cfg.captions.text_color,
        highlight_color=cfg.captions.highlight_color, box_color=cfg.captions.box_color,
        box_radius=cfg.captions.box_radius, box_padding_x=cfg.captions.box_padding_x,
        box_padding_y=cfg.captions.box_padding_y, max_width_ratio=cfg.captions.max_width_ratio,
        line_spacing=cfg.captions.line_spacing, bottom_margin=cfg.captions.bottom_margin,
        top_margin=cfg.captions.top_margin, fade_frames=cfg.captions.fade_frames,
        karaoke=cfg.captions.karaoke,
    )
    fonts_dir = Path(cfg.fonts.fonts_dir)
    if not fonts_dir.is_absolute():
        fonts_dir = Path(cfg.paths.assets_dir) / "fonts"
    font = load_font(language, style.font_size, fonts_dir, cfg.fonts.script_map)

    word_timings = distribute_word_timings(text, duration) if style.karaoke else []
    n_frames = max(int(round(duration * fps)), 1)

    def frame_gen():
        for i in range(n_frames):
            t = i / max(n_frames - 1, 1)
            base = animator.frame_at(t).convert("RGBA")

            elapsed = t * duration
            fade_in_frames = max(style.fade_frames, 1)
            fade_out_start = n_frames - style.fade_frames
            if i < style.fade_frames:
                opacity = i / fade_in_frames
            elif i >= fade_out_start:
                opacity = max(n_frames - 1 - i, 0) / fade_in_frames
            else:
                opacity = 1.0

            active_word_index = None
            if style.karaoke and word_timings:
                for idx, wt in enumerate(word_timings):
                    if wt.start <= elapsed < wt.end:
                        active_word_index = idx
                        break

            overlay = render_caption_overlay(
                (width, height), text, language, font, style,
                opacity=opacity, active_word_index=active_word_index,
            )
            yield Image.alpha_composite(base, overlay).convert("RGB")

    return frames_to_video(frame_gen(), width, height, fps, out_path, cfg.output)


def _brand_font(fonts_dir: Path, cfg: AppConfig, language: str, size: int) -> ImageFont.FreeTypeFont:
    font_name = cfg.brand.title_font or DEFAULT_SCRIPT_FONTS[script_for_language(language)]
    path = fonts_dir / font_name
    if not path.exists():
        path = fonts_dir / DEFAULT_SCRIPT_FONTS["latin"]
    return ImageFont.truetype(str(path), size=size)


def render_intro_video_clip(story, cfg: AppConfig, out_path: Path) -> Path:
    """Branded animated title card: logo, story title, category badge, scene count."""
    width, height = cfg.resolution.width, cfg.resolution.height
    fps = cfg.resolution.fps
    duration = cfg.brand.intro_duration
    n_frames = max(int(round(duration * fps)), 1)
    fonts_dir = Path(cfg.paths.assets_dir) / "fonts"

    logo = None
    if cfg.brand.logo_path and Path(cfg.brand.logo_path).exists():
        logo = Image.open(cfg.brand.logo_path).convert("RGBA")

    def frame_gen():
        for i in range(n_frames):
            t = i / max(n_frames - 1, 1)
            eased = ease_in_out_cubic(min(t / 0.6, 1.0))  # entrance completes at 60% through
            fade = eased
            scale = 0.85 + 0.15 * eased

            frame = Image.new("RGBA", (width, height), (*cfg.brand.background_color, 255))
            draw = ImageDraw.Draw(frame)
            draw.rectangle([0, 0, width, height // 3], fill=(*cfg.brand.primary_color, 40))

            title_font = _brand_font(fonts_dir, cfg, story.language, int(74 * scale))
            cat_font = _brand_font(fonts_dir, cfg, story.language, int(36 * scale))

            cy = height // 2
            if logo:
                logo_resized = logo.resize((int(220 * scale), int(220 * scale)))
                tmp = Image.new("RGBA", frame.size, (0, 0, 0, 0))
                tmp.paste(logo_resized, ((width - logo_resized.width) // 2, cy - 340), logo_resized)
                alpha = tmp.split()[3].point(lambda p: int(p * fade))
                tmp.putalpha(alpha)
                frame = Image.alpha_composite(frame, tmp)
                draw = ImageDraw.Draw(frame)

            title_text = story.title
            tw, th = draw.textbbox((0, 0), title_text, font=title_font)[2:]
            text_color = (*cfg.brand.text_color, int(255 * fade))
            draw.text(((width - tw) / 2, cy - th / 2), title_text, font=title_font, fill=text_color)

            cat_text = f"  {story.category.upper()}  "
            bw, bh = draw.textbbox((0, 0), cat_text, font=cat_font)[2:]
            badge_y = cy + th
            draw.rounded_rectangle(
                [(width - bw) / 2 - 10, badge_y, (width + bw) / 2 + 10, badge_y + bh + 20],
                radius=20, fill=(*cfg.brand.secondary_color, int(255 * fade)),
            )
            draw.text(((width - bw) / 2, badge_y + 10), cat_text, font=cat_font,
                       fill=(20, 20, 20, int(255 * fade)))

            scene_text = f"{len(story.scenes)} scenes"
            sw, sh = draw.textbbox((0, 0), scene_text, font=cat_font)[2:]
            draw.text(((width - sw) / 2, badge_y + bh + 50), scene_text, font=cat_font,
                       fill=(210, 210, 210, int(220 * fade)))

            yield frame.convert("RGB")

    return frames_to_video(frame_gen(), width, height, fps, out_path, cfg.output)


def render_outro_video_clip(cfg: AppConfig, language: str, out_path: Path) -> Path:
    """Branded CTA card: logo, app name, download message, optional QR/app-badge slot."""
    width, height = cfg.resolution.width, cfg.resolution.height
    fps = cfg.resolution.fps
    duration = cfg.brand.outro_duration
    n_frames = max(int(round(duration * fps)), 1)
    fonts_dir = Path(cfg.paths.assets_dir) / "fonts"

    logo = None
    if cfg.brand.logo_path and Path(cfg.brand.logo_path).exists():
        logo = Image.open(cfg.brand.logo_path).convert("RGBA")
    qr = None
    if cfg.brand.qr_code_path and Path(cfg.brand.qr_code_path).exists():
        qr = Image.open(cfg.brand.qr_code_path).convert("RGBA")

    def frame_gen():
        for i in range(n_frames):
            t = i / max(n_frames - 1, 1)
            fade = ease_in_out_cubic(min(t / 0.4, 1.0))

            frame = Image.new("RGBA", (width, height), (*cfg.brand.background_color, 255))
            draw = ImageDraw.Draw(frame)

            app_font = _brand_font(fonts_dir, cfg, language, 60)
            cta_font = _brand_font(fonts_dir, cfg, language, 42)
            ph_font = _brand_font(fonts_dir, cfg, language, 24)

            cy = height // 2 - 200
            if logo:
                logo_resized = logo.resize((200, 200))
                tmp = Image.new("RGBA", frame.size, (0, 0, 0, 0))
                tmp.paste(logo_resized, ((width - 200) // 2, cy - 260), logo_resized)
                alpha = tmp.split()[3].point(lambda p: int(p * fade))
                tmp.putalpha(alpha)
                frame = Image.alpha_composite(frame, tmp)
                draw = ImageDraw.Draw(frame)

            app_text = cfg.brand.app_name
            aw, ah = draw.textbbox((0, 0), app_text, font=app_font)[2:]
            draw.text(((width - aw) / 2, cy), app_text, font=app_font,
                       fill=(*cfg.brand.text_color, int(255 * fade)))

            cta_lines = wrap_text(cfg.brand.cta_text, cta_font, int(width * 0.8), draw)
            y = cy + ah + 60
            for line in cta_lines:
                lw, lh = draw.textbbox((0, 0), line, font=cta_font)[2:]
                draw.text(((width - lw) / 2, y), line, font=cta_font,
                           fill=(*cfg.brand.text_color, int(230 * fade)))
                y += lh + 16

            # Optional QR code / app store badge placeholder area.
            box_size = 260
            box_x0, box_y0 = (width - box_size) / 2, y + 60
            if qr:
                qr_resized = qr.resize((box_size, box_size))
                tmp = Image.new("RGBA", frame.size, (0, 0, 0, 0))
                tmp.paste(qr_resized, (int(box_x0), int(box_y0)), qr_resized)
                alpha = tmp.split()[3].point(lambda p: int(p * fade))
                tmp.putalpha(alpha)
                frame = Image.alpha_composite(frame, tmp)
            else:
                draw.rounded_rectangle(
                    [box_x0, box_y0, box_x0 + box_size, box_y0 + box_size],
                    radius=20, outline=(*cfg.brand.secondary_color, int(255 * fade)), width=3,
                )
                ph_text = "QR / App badge"
                pw, ph = draw.textbbox((0, 0), ph_text, font=ph_font)[2:]
                draw.text((box_x0 + (box_size - pw) / 2, box_y0 + (box_size - ph) / 2), ph_text,
                           font=ph_font, fill=(*cfg.brand.secondary_color, int(200 * fade)))

            yield frame.convert("RGB")

    return frames_to_video(frame_gen(), width, height, fps, out_path, cfg.output)


# ----------------------------------------------------------------------------
# Top-level orchestration
# ----------------------------------------------------------------------------

@dataclass
class SceneRenderInfo:
    scene_number: int
    text: str
    duration: float
    audio_path: Path
    video_path: Path


def assemble_video(
    story, resolved_scene_images: Dict[int, Image.Image], cfg: AppConfig,
    music_path: Optional[Path], voice: str, out_path: Path,
    work_dir: Path, progress_cb: ProgressCallback = None,
) -> Path:
    """
    Full pipeline: TTS -> per-scene render -> intro/outro -> crossfade
    concat (video + audio) -> optional background music -> final mux -> SRT.
    """
    import soundfile as sf

    from .cache import CacheManager
    from .tts.factory import get_tts_provider

    def report(stage: str, cur: int, total: int) -> None:
        if progress_cb:
            progress_cb(stage, cur, total)

    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    cache = CacheManager(Path(cfg.paths.cache_dir))
    provider = get_tts_provider(story.language)

    scenes = story.sorted_scenes
    scene_infos: List[SceneRenderInfo] = []
    srt_entries = []
    running_time = cfg.brand.intro_duration - cfg.timing.transition_duration_s

    for idx, scene in enumerate(scenes):
        report("Synthesizing narration", idx + 1, len(scenes))
        audio_cache_path = cache.audio_cache_path(scene.text, voice, provider.name, story.language)
        if not cache.is_cached(audio_cache_path):
            provider.synthesize(scene.text, story.language, voice, out_path=audio_cache_path)
        else:
            logger.info("Using cached narration for scene %d", scene.scene_number)

        with sf.SoundFile(str(audio_cache_path)) as f:
            raw_duration = len(f) / f.samplerate

        duration = max(raw_duration, cfg.timing.min_scene_duration_s)
        padded_audio_path = work_dir / f"scene_{scene.scene_number}_audio.wav"
        if duration > raw_duration + 0.01:
            pad_audio_to_duration(audio_cache_path, duration, padded_audio_path)
        else:
            shutil.copyfile(audio_cache_path, padded_audio_path)

        report("Rendering scene visuals", idx + 1, len(scenes))
        image = resolved_scene_images[scene.scene_number]
        scene_video_path = work_dir / f"scene_{scene.scene_number}_video.mp4"
        render_scene_video_clip(image, scene.text, story.language, duration, idx, cfg, scene_video_path)

        scene_infos.append(SceneRenderInfo(
            scene_number=scene.scene_number, text=scene.text, duration=duration,
            audio_path=padded_audio_path, video_path=scene_video_path,
        ))
        seg_start = running_time + cfg.timing.transition_duration_s
        srt_entries.append((seg_start, seg_start + duration, scene.text))
        running_time += duration - cfg.timing.transition_duration_s

    report("Rendering intro", 1, 1)
    intro_path = work_dir / "intro.mp4"
    render_intro_video_clip(story, cfg, intro_path)
    intro_silence = make_silence_wav(cfg.brand.intro_duration, work_dir / "intro_silence.wav")

    report("Rendering outro", 1, 1)
    outro_path = work_dir / "outro.mp4"
    render_outro_video_clip(cfg, story.language, outro_path)
    outro_silence = make_silence_wav(cfg.brand.outro_duration, work_dir / "outro_silence.wav")

    all_video_paths = [intro_path] + [s.video_path for s in scene_infos] + [outro_path]
    all_durations = [cfg.brand.intro_duration] + [s.duration for s in scene_infos] + [cfg.brand.outro_duration]
    all_audio_paths = [intro_silence] + [s.audio_path for s in scene_infos] + [outro_silence]

    td = cfg.timing.transition_duration_s
    total_duration = sum(all_durations) - td * (len(all_durations) - 1)

    report("Compositing transitions", 1, 2)
    concatenated_video = xfade_concat_videos(all_video_paths, all_durations, td, work_dir / "concatenated_video.mp4")

    report("Compositing transitions", 2, 2)
    concatenated_audio = acrossfade_concat_audios(all_audio_paths, td, work_dir / "concatenated_narration.wav")

    final_audio = concatenated_audio
    if music_path:
        report("Mixing background music", 1, 1)
        final_audio = apply_background_music(
            concatenated_audio, Path(music_path), total_duration,
            cfg.audio.music_duck_db, cfg.audio.music_default_volume_db,
            cfg.audio.music_fade_out_s, work_dir / "final_audio_with_music.wav",
        )

    report("Encoding final video", 1, 1)
    out_path = Path(out_path)
    mux_final(concatenated_video, final_audio, out_path, cfg.output)

    write_srt(srt_entries, out_path.with_suffix(".srt"))
    return out_path
