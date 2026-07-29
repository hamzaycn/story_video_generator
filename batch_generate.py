#!/usr/bin/env python3
"""
Batch video generator for story_prompt_studio output format.

Usage:
    # Single story directory
    python batch_generate.py --input story_prompt_studio/output/luna_and_the_talking_tree_3
    
    # Process all stories in output folder
    python batch_generate.py --input story_prompt_studio/output --recursive
    
    # Watch mode - automatically regenerate when story.json changes
    python batch_generate.py --input story_prompt_studio/output/luna_and_the_talking_tree_3 --watch
"""
from __future__ import annotations

import json
import logging
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
from rich.table import Table

sys.path.insert(0, str(Path(__file__).parent))

from core.cache import CacheManager
from core.config import AppConfig, load_config
from core.image_manager import get_image
from core.story_loader import load_story, Story
from core.subtitles import resolve_font_path
from core.tts.factory import get_tts_provider
from core.video_builder import assemble_video

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = typer.Typer(add_completion=False)
console = Console()

logging.basicConfig(
    level="INFO", format="%(message)s", datefmt="[%X]",
    handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=False)],
)
logger = logging.getLogger("batch_generator")


def resolve_local_image_paths(story_data: Story, story_dir: Path) -> Story:
    """
    Automatically resolve relative image paths for story_prompt_studio format.
    
    Converts:
        imageUrl: "cover.png" -> "D:/story_prompt_studio/output/story_name/assets/images/cover.png"
        imageUrl: "scene1.png" -> "D:/story_prompt_studio/output/story_name/assets/images/scene1.png"
    """
    assets_dir = story_dir / "assets" / "images"
    
    # Resolve cover image
    if story_data.cover_image_url:
        if not story_data.cover_image_url.startswith(("http://", "https://")):
            # Assume it's a local filename
            local_path = assets_dir / story_data.cover_image_url
            if local_path.exists():
                story_data.cover_image_url = str(local_path.resolve())
                logger.debug(f"Resolved cover: {story_data.cover_image_url}")
    
    # Resolve scene images
    for scene in story_data.scenes:
        if scene.image_url and not scene.image_url.startswith(("http://", "https://")):
            local_path = assets_dir / scene.image_url
            if local_path.exists():
                scene.image_url = str(local_path.resolve())
                logger.debug(f"Resolved scene {scene.scene_number}: {scene.image_url}")
    
    return story_data


def find_story_directories(root: Path, recursive: bool = False) -> list[Path]:
    """Find all directories containing a story.json file."""
    story_dirs = []
    
    if recursive:
        for story_json in root.rglob("story.json"):
            story_dirs.append(story_json.parent)
    else:
        # Check if root itself contains story.json
        if (root / "story.json").exists():
            story_dirs.append(root)
        else:
            # Check immediate subdirectories
            for child in root.iterdir():
                if child.is_dir() and (child / "story.json").exists():
                    story_dirs.append(child)
    
    return sorted(story_dirs)


def generate_video_for_story(
    story_dir: Path,
    cfg: AppConfig,
    music: Optional[Path],
    voice: str,
    output_dir: Path,
) -> Optional[Path]:
    """Generate a single video from a story directory."""
    try:
        story_path = story_dir / "story.json"
        
        # Load and resolve paths
        story_data = load_story(story_path)
        story_data = resolve_local_image_paths(story_data, story_dir)
        
        # Use story directory name as output filename
        output_filename = f"{story_dir.name}.mp4"
        output_path = output_dir / output_filename
        
        console.print(f"\n[bold cyan]Processing:[/bold cyan] {story_data.title}")
        console.print(f"[dim]Story ID: {story_data.id}[/dim]")
        console.print(f"[dim]Scenes: {len(story_data.scenes)}[/dim]")
        
        # Resolve images
        cache = CacheManager(Path(cfg.paths.cache_dir))
        resolved_images = {}
        
        with Progress(
            SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
            BarColumn(), TimeElapsedColumn(), console=console,
        ) as progress:
            task = progress.add_task("Loading images", total=len(story_data.scenes))
            for scene in story_data.sorted_scenes:
                url = story_data.image_url_for_scene(scene)
                img = get_image(
                    url, cache, brand_color=tuple(cfg.brand.primary_color),
                    label=f"Scene {scene.scene_number}",
                    fonts_dir=Path(cfg.paths.assets_dir) / "fonts",
                    placeholder_size=(cfg.resolution.width, cfg.resolution.height),
                )
                resolved_images[scene.scene_number] = img
                progress.advance(task)
        
        # Generate video
        with tempfile.TemporaryDirectory(prefix=f"story_video_{story_dir.name}_") as tmp:
            result_path = assemble_video(
                story_data, resolved_images, cfg, music, voice, output_path,
                work_dir=Path(tmp),
                progress_cb=lambda stage, cur, total: console.print(
                    f"[cyan]{stage}[/cyan]: {cur}/{total}"
                ),
            )
        
        console.print(f"[bold green]✓ Complete:[/bold green] {result_path}")
        return result_path
        
    except Exception as e:
        console.print(f"[bold red]✗ Failed:[/bold red] {story_dir.name}")
        logger.exception(f"Error processing {story_dir}")
        return None


@app.command()
def main(
    input: Path = typer.Option(
        ..., "--input", "-i", 
        help="Story directory or parent directory containing multiple stories"
    ),
    output_dir: Optional[Path] = typer.Option(
        None, "--output", "-o",
        help="Output directory for videos (default: input/videos)"
    ),
    recursive: bool = typer.Option(
        False, "--recursive", "-r",
        help="Recursively search for story.json files"
    ),
    watch: bool = typer.Option(
        False, "--watch", "-w",
        help="Watch for changes and regenerate automatically"
    ),
    music: Optional[Path] = typer.Option(None, "--music", help="Background music file"),
    voice: str = typer.Option("hannah", "--voice", help="TTS voice"),
    config: Optional[Path] = typer.Option(None, "--config", help="Path to config.yaml"),
    log_level: str = typer.Option("INFO", "--log-level"),
  parallel: bool = typer.Option(
        False, "--parallel", "-p",
        help="Process multiple stories in parallel"
    ),
    workers: int = typer.Option(
        2, "--workers",
        help="Number of parallel workers (default: 2)"
    ),
) -> None:
    """Batch generate videos from story_prompt_studio output."""
    logging.getLogger("batch_generator").setLevel(log_level.upper())
    
    if not input.exists():
        console.print(f"[bold red]Error:[/bold red] Input path does not exist: {input}")
        raise typer.Exit(code=1)
    
    # Load config
    cfg = load_config(config)
    
    # Determine output directory
    if output_dir is None:
        if input.is_dir():
            output_dir = input / "videos"
        else:
            output_dir = input.parent / "videos"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    console.rule("[bold cyan]Story Video Batch Generator")
    
    if watch:
        watch_and_regenerate(input, output_dir, cfg, music, voice, recursive)
    else:
        batch_generate(input, output_dir, cfg, music, voice, recursive)

    if parallel and len(story_dirs) > 1:
        batch_generate_parallel(story_dirs, output_dir, cfg, music, voice, workers)
    else:
        batch_generate(input_path, output_dir, cfg, music, voice, recursive)

def batch_generate_parallel(
    story_dirs: list[Path],
    output_dir: Path,
    cfg: AppConfig,
    music: Optional[Path],
    voice: str,
    max_workers: int,
) -> None:
    """Generate videos in parallel."""
    from concurrent.futures import ProcessPoolExecutor, as_completed
    
    console.print(f"Processing {len(story_dirs)} stories with {max_workers} workers")
    
    results = []
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                generate_video_for_story, story_dir, cfg, music, voice, output_dir
            ): story_dir for story_dir in story_dirs
        }
        
        for future in as_completed(futures):
            story_dir = futures[future]
            try:
                result = future.result()
                results.append((story_dir.name, result is not None, result))
            except Exception as e:
                console.print(f"[red]Failed: {story_dir.name}[/red]")
                results.append((story_dir.name, False, None))

def batch_generate(
    input_path: Path,
    output_dir: Path,
    cfg: AppConfig,
    music: Optional[Path],
    voice: str,
    recursive: bool,
) -> None:
    """Generate videos for all found stories."""
    story_dirs = find_story_directories(input_path, recursive)
    
    if not story_dirs:
        console.print(f"[yellow]No story.json files found in {input_path}[/yellow]")
        return
    
    console.print(f"Found {len(story_dirs)} stor{'y' if len(story_dirs) == 1 else 'ies'}")
    
    results = []
    start_time = time.time()
    
    for idx, story_dir in enumerate(story_dirs, 1):
        console.rule(f"[cyan]{idx}/{len(story_dirs)}")
        result = generate_video_for_story(story_dir, cfg, music, voice, output_dir)
        results.append((story_dir.name, result is not None, result))
    
    # Summary table
    elapsed = time.time() - start_time
    console.rule("[bold green]Summary")
    
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Story", style="cyan")
    table.add_column("Status", justify="center")
    table.add_column("Output", style="dim")
    
    successful = 0
    for name, success, path in results:
        if success:
            successful += 1
            table.add_row(name, "[green]✓[/green]", str(path) if path else "")
        else:
            table.add_row(name, "[red]✗[/red]", "Failed")
    
    console.print(table)
    console.print(f"\n[bold]{successful}/{len(results)}[/bold] successful")
    console.print(f"[dim]Total time: {elapsed:.1f}s ({elapsed/len(results):.1f}s avg)[/dim]")


def watch_and_regenerate(
    input_path: Path,
    output_dir: Path,
    cfg: AppConfig,
    music: Optional[Path],
    voice: str,
    recursive: bool,
) -> None:
    """Watch for story.json changes and auto-regenerate."""
    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
    except ImportError:
        console.print(
            "[bold red]Error:[/bold red] Watch mode requires watchdog. "
            "Install with: pip install watchdog"
        )
        raise typer.Exit(code=1)
    
    class StoryChangeHandler(FileSystemEventHandler):
        def __init__(self):
            self.last_modified = {}
            self.debounce_seconds = 1.0
        
        def on_modified(self, event):
            if event.is_directory or not event.src_path.endswith("story.json"):
                return
            
            # Debounce rapid consecutive saves
            now = time.time()
            if event.src_path in self.last_modified:
                if now - self.last_modified[event.src_path] < self.debounce_seconds:
                    return
            
            self.last_modified[event.src_path] = now
            story_dir = Path(event.src_path).parent
            
            console.print(f"\n[yellow]Change detected:[/yellow] {story_dir.name}")
            generate_video_for_story(story_dir, cfg, music, voice, output_dir)
    
    console.print(f"[bold cyan]Watching for changes in:[/bold cyan] {input_path}")
    console.print("[dim]Press Ctrl+C to stop[/dim]\n")
    
    event_handler = StoryChangeHandler()
    observer = Observer()
    observer.schedule(event_handler, str(input_path), recursive=True)
    observer.start()
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopping watch mode...[/yellow]")
        observer.stop()
    observer.join()


if __name__ == "__main__":
    app()