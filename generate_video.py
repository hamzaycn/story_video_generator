#!/usr/bin/env python3
"""
CLI entry point for the story video generator.

Usage:
    python generate_video.py \
        --story story.json \
        --output output/story_123.mp4 \
        --music assets/music/calm_piano.mp3 \
        --voice hannah \
        --config config.yaml

    # Fast validation / duration estimate, no rendering:
    python generate_video.py --story story.json --output output/x.mp4 --dry-run
"""
from __future__ import annotations

import logging
import sys
import tempfile
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

sys.path.insert(0, str(Path(__file__).parent))

from core.cache import CacheManager  # noqa: E402
from core.config import AppConfig, load_config  # noqa: E402
from core.image_manager import get_image  # noqa: E402
from core.story_loader import load_story , resolve_local_paths # noqa: E402
from core.subtitles import resolve_font_path  # noqa: E402
from core.tts.base import TTSError, UnsupportedLanguageError  # noqa: E402
from core.tts.factory import get_tts_provider  # noqa: E402
from core.video_builder import assemble_video  # noqa: E402

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # .env support is optional; env vars set another way still work

app = typer.Typer(add_completion=False, help="Generate branded, narrated, captioned vertical story videos.")
console = Console()

logging.basicConfig(
    level="INFO", format="%(message)s", datefmt="[%X]",
    handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=False)],
)
logger = logging.getLogger("story_video_generator")


@app.command()
def main(
    story: Path = typer.Option(..., "--story", exists=True, help="Path to story JSON file."),
    output: Path = typer.Option(..., "--output", help="Path to write the final MP4."),
    music: Optional[Path] = typer.Option(None, "--music", help="Optional background music file."),
    voice: Optional[str] = typer.Option(None, "--voice", help="TTS voice name (overrides config default_voice)."),
    config: Optional[Path] = typer.Option(None, "--config", help="Path to config.yaml."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Validate story/assets/fonts and estimate duration without rendering."),
    log_level: str = typer.Option("INFO", "--log-level", help="DEBUG, INFO, WARNING, or ERROR."),
) -> None:
    logging.getLogger("story_video_generator").setLevel(log_level.upper())
    console.rule("[bold cyan]Story Video Generator")

    cfg: AppConfig = load_config(config)
    resolved_voice = voice or cfg.default_voice

    # --- 1. Story validation ---
    try:
        story_data = load_story(story)
        story_data = resolve_local_paths(story_data, story.parent)   # NEW
    except Exception as e:
        console.print(f"[bold red]Story validation failed:[/bold red] {e}")
        raise typer.Exit(code=1)

    console.print(
        f"[green]Loaded[/green] '{story_data.title}' — {len(story_data.scenes)} scene(s), "
        f"language={story_data.language}, imageMode={story_data.image_mode.value}"
    )

    # --- 2. Resolve TTS provider up front so language/config errors surface immediately ---
    try:
        provider = get_tts_provider(story_data.language)
        console.print(f"[green]TTS provider:[/green] {provider.name}")
    except UnsupportedLanguageError as e:
        console.print(f"[bold red]{e}[/bold red]")
        raise typer.Exit(code=1)

    # --- 3. Resolve fonts ---
    fonts_dir = Path(cfg.paths.assets_dir) / "fonts"
    font_path = resolve_font_path(story_data.language, fonts_dir, cfg.fonts.script_map)
    console.print(f"[green]Font resolved:[/green] {font_path}")

    # --- 4. Resolve / cache images (graceful placeholder fallback per-scene) ---
    cache = CacheManager(Path(cfg.paths.cache_dir))
    console.print("[cyan]Resolving scene images...[/cyan]")
    resolved_images = {}
    with Progress(
        SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
        BarColumn(), TimeElapsedColumn(), console=console,
    ) as progress:
        task = progress.add_task("Fetching images", total=len(story_data.scenes))
        for scene in story_data.sorted_scenes:
            url = story_data.image_url_for_scene(scene)
            img = get_image(
                url, cache, brand_color=tuple(cfg.brand.primary_color),
                label=f"Scene {scene.scene_number}", fonts_dir=fonts_dir,
                placeholder_size=(cfg.resolution.width, cfg.resolution.height),
            )
            resolved_images[scene.scene_number] = img
            progress.advance(task)
        cover_image = None
        if story_data.cover_image_url:
            console.print("[cyan]Resolving cover image for intro...[/cyan]")
            cover_image = get_image(
                story_data.cover_image_url, cache, brand_color=tuple(cfg.brand.primary_color),
                label="Cover", fonts_dir=fonts_dir,
                placeholder_size=(cfg.resolution.width, cfg.resolution.height),
            )


    # --- 5. Dry run: validate + estimate, skip rendering ---
    if dry_run:
        words_per_second = 2.5  # rough ~150 wpm narration estimate, not real TTS timing
        est_scene_time = sum(
            max(len(s.text.split()) / words_per_second, cfg.timing.min_scene_duration_s)
            for s in story_data.scenes
        )
        n_transitions = len(story_data.scenes) + 1
        est_total = (
            cfg.brand.intro_duration + est_scene_time + cfg.brand.outro_duration
            - cfg.timing.transition_duration_s * n_transitions
        )
        console.rule("[bold yellow]Dry run summary")
        console.print(f"Scenes: {len(story_data.scenes)}")
        console.print(f"Estimated total duration: ~{est_total:.1f}s "
                       f"(rough word-count estimate — real TTS timing will differ)")
        console.print(f"Output would be written to: {output}")
        console.print("[green]Dry run OK — story, images, and fonts all resolved successfully.[/green]")
        raise typer.Exit(code=0)

    # --- 6. Full render ---
    def progress_cb(stage: str, cur: int, total: int) -> None:
        console.print(f"[cyan]{stage}[/cyan]: {cur}/{total}")

    with tempfile.TemporaryDirectory(prefix="story_video_") as tmp:
        try:
            result_path = assemble_video(
                story_data, resolved_images, cfg, music, resolved_voice, output,
                work_dir=Path(tmp), progress_cb=progress_cb,
                cover_image=cover_image,   # NEW
            )
        except TTSError as e:
            console.print(f"[bold red]TTS error:[/bold red] {e}")
            raise typer.Exit(code=1)
        except Exception as e:
            logger.exception("Video generation failed")
            console.print(f"[bold red]Video generation failed:[/bold red] {e}")
            raise typer.Exit(code=1)

    console.rule("[bold green]Done")
    console.print(f"[bold green]Video written to:[/bold green] {result_path}")
    console.print(f"[bold green]Subtitles written to:[/bold green] {result_path.with_suffix('.srt')}")


if __name__ == "__main__":
    app()
