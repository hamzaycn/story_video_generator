#!/usr/bin/env python3
"""
FastAPI web interface for story video generator.

Usage:
    python web/app.py
    # Then open http://localhost:8080 in your browser
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.config import load_config, AppConfig
from core.cache import CacheManager
from core.story_loader import load_story
from core.image_manager import get_image
from core.video_builder import assemble_video

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("web_interface")

app = FastAPI(title="Story Video Generator")

# CORS for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Job management
class JobStatus(BaseModel):
    job_id: str
    status: str  # queued, processing, complete, failed
    story_name: str
    progress: int  # 0-100
    current_stage: str
    output_path: Optional[str] = None
    error: Optional[str] = None
    created_at: str
    completed_at: Optional[str] = None

class JobManager:
    def __init__(self):
        self.jobs: Dict[str, JobStatus] = {}
        self.active_connections: Dict[str, WebSocket] = {}
    
    def create_job(self, story_name: str) -> str:
        job_id = str(uuid.uuid4())[:8]
        self.jobs[job_id] = JobStatus(
            job_id=job_id,
            status="queued",
            story_name=story_name,
            progress=0,
            current_stage="Initializing",
            created_at=datetime.now().isoformat(),
        )
        return job_id
    
    async def update_job(self, job_id: str, **kwargs):
        if job_id in self.jobs:
            for key, value in kwargs.items():
                setattr(self.jobs[job_id], key, value)
            # Broadcast to connected websocket
            if job_id in self.active_connections:
                try:
                    await self.active_connections[job_id].send_json(
                        self.jobs[job_id].dict()
                    )
                except Exception as e:
                    logger.warning(f"Failed to send update: {e}")
    
    def get_job(self, job_id: str) -> Optional[JobStatus]:
        return self.jobs.get(job_id)
    
    def list_jobs(self) -> list[JobStatus]:
        return sorted(self.jobs.values(), key=lambda j: j.created_at, reverse=True)

job_manager = JobManager()

# Paths
BASE_DIR = Path(__file__).parent.parent
WEB_DIR = Path(__file__).parent
STATIC_DIR = WEB_DIR / "static"
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "output"

UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)
STATIC_DIR.mkdir(exist_ok=True)

# Mount static files
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/output", StaticFiles(directory=str(OUTPUT_DIR)), name="output")


@app.get("/", response_class=HTMLResponse)
async def read_root():
    """Serve the main interface."""
    html_path = STATIC_DIR / "index.html"
    if html_path.exists():
        return HTMLResponse(content=html_path.read_text(encoding='utf-8'), status_code=200)  # Add encoding='utf-8'
    return HTMLResponse(content="<h1>Interface not found. Create static/index.html</h1>")

@app.get("/api/presets")
async def get_presets():
    """Get available render presets."""
    presets_path = BASE_DIR / "presets.yaml"
    presets = {
        "draft": {
            "name": "Draft",
            "description": "Fast preview (24fps, lower quality)",
            "icon": "⚡"
        },
        "social": {
            "name": "Social Media",
            "description": "Optimized for TikTok/Reels/Shorts",
            "icon": "📱"
        },
        "premium": {
            "name": "Premium",
            "description": "Highest quality (60fps, slow encode)",
            "icon": "✨"
        }
    }
    
    if presets_path.exists():
        import yaml
        with open(presets_path) as f:
            preset_configs = yaml.safe_load(f) or {}
        # Merge with metadata
        for key in preset_configs:
            if key not in presets:
                presets[key] = {"name": key.title(), "description": "Custom preset", "icon": "🎬"}
    
    return presets


@app.get("/api/voices")
async def get_voices():
    """Get available TTS voices."""
    return {
        "groq": {
            "label": "Groq Orpheus (English)",
            "voices": [
                {"id": "hannah", "name": "Hannah", "gender": "F", "style": "Clear, friendly"},
                {"id": "diana", "name": "Diana", "gender": "F", "style": "Warm, narrative"},
                {"id": "autumn", "name": "Autumn", "gender": "F", "style": "Soft, calming"},
                {"id": "austin", "name": "Austin", "gender": "M", "style": "Deep, authoritative"},
                {"id": "daniel", "name": "Daniel", "gender": "M", "style": "Natural, conversational"},
                {"id": "troy", "name": "Troy", "gender": "M", "style": "Energetic, upbeat"},
            ]
        }
    }


@app.get("/api/stories")
async def list_stories(path: str = "story_prompt_studio/output"):
    """List available stories in a directory."""
    base_path = BASE_DIR.parent / path
    if not base_path.exists():
        return {"stories": []}
    
    stories = []
    for item in base_path.iterdir():
        if item.is_dir():
            story_json = item / "story.json"
            if story_json.exists():
                try:
                    with open(story_json) as f:
                        data = json.load(f)
                    
                    # Get cover image
                    cover = None
                    assets_dir = item / "assets" / "images"
                    if assets_dir.exists():
                        cover_candidates = ["cover.png", "cover.jpg", "scene1.png", "scene1.jpg"]
                        for candidate in cover_candidates:
                            cover_path = assets_dir / candidate
                            if cover_path.exists():
                                cover = f"/api/story-image/{path}/{item.name}/{candidate}"
                                break
                    
                    stories.append({
                        "id": data.get("id", item.name),
                        "name": item.name,
                        "title": data.get("title", item.name),
                        "category": data.get("category", "general"),
                        "language": data.get("language", "en-US"),
                        "scenes": len(data.get("scenes", [])),
                        "path": str(item.relative_to(BASE_DIR.parent)),
                        "cover": cover,
                    })
                except Exception as e:
                    logger.warning(f"Failed to parse {story_json}: {e}")
    
    return {"stories": sorted(stories, key=lambda s: s["name"])}


@app.get("/api/story-image/{path:path}/{story}/{filename}")
async def get_story_image(path: str, story: str, filename: str):
    """Serve story images."""
    image_path = BASE_DIR.parent / path / story / "assets" / "images" / filename
    if not image_path.exists():
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(image_path)


@app.post("/api/upload")
async def upload_story(file: UploadFile = File(...)):
    """Upload a story.json file."""
    try:
        # Create a unique directory for this story
        story_id = str(uuid.uuid4())[:8]
        story_dir = UPLOAD_DIR / story_id
        story_dir.mkdir(parents=True)
        
        # Save the uploaded file
        story_path = story_dir / "story.json"
        with open(story_path, "wb") as f:
            content = await file.read()
            f.write(content)
        
        # Parse to get story name
        with open(story_path) as f:
            data = json.load(f)
        
        return {
            "story_id": story_id,
            "name": data.get("title", story_id),
            "path": str(story_dir.relative_to(BASE_DIR.parent)),
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/generate")
async def generate_video(
    story_path: str = Form(...),
    preset: str = Form("draft"),
    voice: str = Form("hannah"),
    music_path: Optional[str] = Form(None),
):
    """Queue a video generation job."""
    try:
        # Resolve story directory
        story_dir = BASE_DIR.parent / story_path
        if not story_dir.exists():
            # Try as absolute path
            story_dir = Path(story_path)
            if not story_dir.exists():
                raise HTTPException(status_code=404, detail="Story directory not found")
        
        story_json = story_dir / "story.json"
        if not story_json.exists():
            raise HTTPException(status_code=404, detail="story.json not found")
        
        # Create job
        with open(story_json) as f:
            data = json.load(f)
        story_name = data.get("title", story_dir.name)
        
        job_id = job_manager.create_job(story_name)
        
        # Start generation in background
        asyncio.create_task(
            run_generation(job_id, story_dir, preset, voice, music_path)
        )
        
        return {"job_id": job_id, "status": "queued"}
    
    except Exception as e:
        logger.exception("Failed to queue job")
        raise HTTPException(status_code=500, detail=str(e))


async def run_generation(
    job_id: str,
    story_dir: Path,
    preset: str,
    voice: str,
    music_path: Optional[str],
):
    """Run video generation (background task)."""
    try:
        await job_manager.update_job(
            job_id, status="processing", current_stage="Loading configuration"
        )
        
        # Load config with preset
        cfg = load_config(BASE_DIR / "config.yaml")
        
        # Apply preset
        if preset and preset != "default":
            presets_path = BASE_DIR / "presets.yaml"
            if presets_path.exists():
                import yaml
                with open(presets_path) as f:
                    presets = yaml.safe_load(f) or {}
                if preset in presets:
                    # Simple merge (you can use the deep_merge from earlier)
                    preset_cfg = presets[preset]
                    for key, value in preset_cfg.items():
                        if hasattr(cfg, key):
                            setattr(cfg, key, value)
        
        await job_manager.update_job(job_id, progress=5, current_stage="Loading story")
        
        # Load story
        from batch_generate import resolve_local_image_paths
        story_data = load_story(story_dir / "story.json")
        story_data = resolve_local_image_paths(story_data, story_dir)
        
        await job_manager.update_job(job_id, progress=10, current_stage="Resolving images")
        
        # Resolve images
        cache = CacheManager(Path(cfg.paths.cache_dir))
        resolved_images = {}
        
        for idx, scene in enumerate(story_data.sorted_scenes):
            url = story_data.image_url_for_scene(scene)
            img = get_image(
                url, cache,
                brand_color=tuple(cfg.brand.primary_color),
                label=f"Scene {scene.scene_number}",
                fonts_dir=BASE_DIR / "assets" / "fonts",
                placeholder_size=(cfg.resolution.width, cfg.resolution.height),
                skip_cache_for_local=True,
            )
            resolved_images[scene.scene_number] = img
            progress = 10 + int(20 * (idx + 1) / len(story_data.scenes))
            await job_manager.update_job(job_id, progress=progress)
        
        # Output path
        output_filename = f"{story_dir.name}_{job_id}.mp4"
        output_path = OUTPUT_DIR / output_filename
        
        # Progress callback
        async def progress_cb(stage: str, cur: int, total: int):
            # Map stages to progress ranges
            stage_ranges = {
                "Synthesizing narration": (30, 50),
                "Rendering scene": (50, 70),
                "Compositing transitions": (70, 85),
                "Mixing background music": (85, 90),
                "Encoding final video": (90, 98),
            }
            
            for stage_key, (start, end) in stage_ranges.items():
                if stage_key.lower() in stage.lower():
                    progress = start + int((end - start) * cur / total)
                    await job_manager.update_job(
                        job_id, progress=progress, current_stage=stage
                    )
                    break
        
        await job_manager.update_job(job_id, progress=30, current_stage="Generating video")
        
        # Generate video
        music = Path(music_path) if music_path else None
        with tempfile.TemporaryDirectory(prefix=f"story_video_{job_id}_") as tmp:
            result_path = await asyncio.to_thread(
                assemble_video,
                story_data,
                resolved_images,
                cfg,
                music,
                voice,
                output_path,
                work_dir=Path(tmp),
                progress_cb=lambda stage, cur, total: asyncio.create_task(
                    progress_cb(stage, cur, total)
                ),
            )
        
        await job_manager.update_job(
            job_id,
            status="complete",
            progress=100,
            current_stage="Complete",
            output_path=f"/output/{output_filename}",
            completed_at=datetime.now().isoformat(),
        )
        
    except Exception as e:
        logger.exception(f"Job {job_id} failed")
        await job_manager.update_job(
            job_id,
            status="failed",
            current_stage="Failed",
            error=str(e),
            completed_at=datetime.now().isoformat(),
        )


@app.websocket("/ws/job/{job_id}")
async def websocket_endpoint(websocket: WebSocket, job_id: str):
    """WebSocket for real-time job updates."""
    await websocket.accept()
    job_manager.active_connections[job_id] = websocket
    
    # Send initial state
    job = job_manager.get_job(job_id)
    if job:
        await websocket.send_json(job.dict())
    
    try:
        while True:
            # Keep connection alive
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        if job_id in job_manager.active_connections:
            del job_manager.active_connections[job_id]


@app.get("/api/jobs")
async def list_jobs():
    """List all jobs."""
    return {"jobs": [job.dict() for job in job_manager.list_jobs()]}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    """Get job status."""
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.dict()


@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str):
    """Delete a job."""
    job = job_manager.get_job(job_id)
    if job and job.output_path:
        try:
            output_file = BASE_DIR.parent / job.output_path.lstrip("/")
            if output_file.exists():
                output_file.unlink()
        except Exception as e:
            logger.warning(f"Failed to delete output: {e}")
    
    if job_id in job_manager.jobs:
        del job_manager.jobs[job_id]
    
    return {"status": "deleted"}


if __name__ == "__main__":
    print("=" * 60)
    print("Story Video Generator - Web Interface")
    print("=" * 60)
    print("\n🌐 Open your browser to: http://localhost:8080")
    print("\n📁 Story directories will be scanned from: story_prompt_studio/output")
    print("\n Press Ctrl+C to stop\n")
    
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")