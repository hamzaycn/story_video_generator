#!/usr/bin/env python3
"""Pre-cache TTS for all scenes to speed up subsequent renders."""

def warmup_story_cache(story_dir: Path, voice: str, cfg: AppConfig):
    """Pre-generate and cache all TTS audio for a story."""
    story_data = load_story(story_dir / "story.json")
    story_data = resolve_local_image_paths(story_data, story_dir)
    
    cache = CacheManager(Path(cfg.paths.cache_dir))
    provider = get_tts_provider(story_data.language)
    
    console.print(f"[cyan]Warming cache for:[/cyan] {story_data.title}")
    
    for scene in story_data.sorted_scenes:
        audio_path = cache.audio_cache_path(
            scene.text, voice, provider.name, story_data.language
        )
        if not cache.is_cached(audio_path):
            console.print(f"  Scene {scene.scene_number}... ", end="")
            provider.synthesize(scene.text, story_data.language, voice, audio_path)
            console.print("[green]✓[/green]")
        else:
            console.print(f"  Scene {scene.scene_number}... [dim]cached[/dim]")
    
    console.print("[green]Cache warmed![/green]")