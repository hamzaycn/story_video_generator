#!/usr/bin/env python3
"""
Batch-render every story folder under a root directory.

Expects the structure you already produce:
  root/
    luna_and_the_talking_tree_3/
      story.json
      assets/images/cover.png, scene1.png, ...
    another_story/
      story.json
      assets/images/...

Usage (local or Colab):
  python batch_generate.py --root output --out-root rendered --config config.yaml
"""
from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

import typer
from rich.console import Console

sys.path.insert(0, str(Path(__file__).parent))

from core.cache import CacheManager
from core.config import load_config
from core.image_manager import get_image
from core.story_loader import load_story, resolve_local_paths
from core.subtitles import resolve_font_path
from core.tts.factory import get_tts_provider
from core.video_builder import assemble_video

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

console = Console()
app = typer.Typer(add_completion=False)


@app.command()
def main(
    root: Path = typer.Option(..., "--root", exists=True, file_okay=False,
                                help="Folder containing one subfolder per story."),
    out_root: Path = typer.Option(Path("rendered"), "--out-root",
                                    help="Where to write <story_folder>.mp4 files."),
    config: Path = typer.Option(None, "--config"),
    voice: str = typer.Option(None, "--voice"),
    music: Path = typer.Option(None, "--music"),
) -> None:
    cfg = load_config(config)
    resolved_voice = voice or cfg.default_voice
    out_root.mkdir(parents=True, exist_ok=True)
    cache = CacheManager(Path(cfg.paths.cache_dir))
    fonts_dir = Path(cfg.paths.assets_dir) / "fonts"

    story_dirs = sorted(
        d for d in root.iterdir() if d.is_dir() and (d / "story.json").exists()
    )
    console.print(f"[cyan]Found {len(story_dirs)} story folder(s) under {root}[/cyan]")

    results = {"ok": [], "failed": []}

    for i, story_dir in enumerate(story_dirs, start=1):
        story_path = story_dir / "story.json"
        out_path = out_root / f"{story_dir.name}.mp4"

        if out_path.exists() and out_path.stat().st_size > 0:
            console.print(f"[yellow]({i}/{len(story_dirs)}) {story_dir.name} -- already rendered, skipping[/yellow]")
            results["ok"].append(story_dir.name)
            continue

        console.rule(f"[bold cyan]({i}/{len(story_dirs)}) {story_dir.name}")
        try:
            story_data = load_story(story_path)
            story_data = resolve_local_paths(story_data, story_dir)

            # provider check up front, per story (in case language varies)
            get_tts_provider(story_data.language)
            resolve_font_path(story_data.language, fonts_dir, cfg.fonts.script_map)

            resolved_images = {}
            for scene in story_data.sorted_scenes:
                url = story_data.image_url_for_scene(scene)
                resolved_images[scene.scene_number] = get_image(
                    url, cache, brand_color=tuple(cfg.brand.primary_color),
                    label=f"Scene {scene.scene_number}", fonts_dir=fonts_dir,
                    placeholder_size=(cfg.resolution.width, cfg.resolution.height),
                )

            cover_image = None
            if story_data.cover_image_url:
                cover_image = get_image(
                    story_data.cover_image_url, cache, brand_color=tuple(cfg.brand.primary_color),
                    label="Cover", fonts_dir=fonts_dir,
                    placeholder_size=(cfg.resolution.width, cfg.resolution.height),
                )

            out_path = out_root / f"{story_dir.name}.mp4"

            def progress_cb(stage, cur, total, _name=story_dir.name):
                console.print(f"  [{_name}] {stage}: {cur}/{total}")

            with tempfile.TemporaryDirectory(prefix=f"story_{story_dir.name}_") as tmp:
                assemble_video(
                    story_data, resolved_images, cfg, music, resolved_voice, out_path,
                    work_dir=Path(tmp), progress_cb=progress_cb, cover_image=cover_image,
                )

            console.print(f"[green]✔ {story_dir.name} -> {out_path}[/green]")
            results["ok"].append(story_dir.name)

        except Exception as e:
            console.print(f"[bold red]✘ {story_dir.name} failed: {e}[/bold red]")
            traceback.print_exc()
            results["failed"].append(story_dir.name)
            continue  # NEVER let one bad story kill the whole batch

    console.rule("[bold yellow]Batch summary")
    console.print(f"[green]Succeeded ({len(results['ok'])}):[/green] {results['ok']}")
    console.print(f"[red]Failed ({len(results['failed'])}):[/red] {results['failed']}")


if __name__ == "__main__":
    app()