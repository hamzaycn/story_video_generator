#!/usr/bin/env python3
"""
Windows Desktop GUI for Story Video Generator

Usage:
    python gui_app.py
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from queue import Queue
from tkinter import filedialog, messagebox
from typing import Dict, Optional

import customtkinter as ctk
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))

from core.cache import CacheManager
from core.config import AppConfig, load_config
from core.image_manager import get_image
from core.story_loader import load_story
from core.video_builder import assemble_video

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("gui_app")

# Set appearance
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

BASE_DIR = Path(__file__).parent
DEFAULT_STORY_PATH = BASE_DIR.parent / "story_prompt_studio" / "output"


def resolve_local_image_paths(story_data, story_dir: Path):
    """
    Resolve relative image paths to absolute paths.
    
    Converts:
        imageUrl: "assets/images/scene1.png" 
        -> "D:/story_prompt_studio/output/story_name/assets/images/scene1.png"
    """
    # Resolve cover image
    if story_data.cover_image_url:
        if not story_data.cover_image_url.startswith(("http://", "https://")):
            # It's a relative path
            local_path = story_dir / story_data.cover_image_url
            if local_path.exists():
                story_data.cover_image_url = str(local_path.resolve())
                logger.debug(f"Resolved cover: {story_data.cover_image_url}")
            else:
                logger.warning(f"Cover image not found: {local_path}")
    
    # Resolve scene images
    for scene in story_data.scenes:
        if scene.image_url and not scene.image_url.startswith(("http://", "https://")):
            local_path = story_dir / scene.image_url
            if local_path.exists():
                scene.image_url = str(local_path.resolve())
                logger.debug(f"Resolved scene {scene.scene_number}: {scene.image_url}")
            else:
                logger.warning(f"Scene {scene.scene_number} image not found: {local_path}")
    
    return story_data


class StoryCard(ctk.CTkFrame):
    """A card widget representing a story."""
    
    def __init__(self, parent, story_data: dict, on_select):
        super().__init__(parent, fg_color="#1a1828", corner_radius=10, border_width=2, border_color="#3a3850")
        
        self.story_data = story_data
        self.on_select = on_select
        self.selected = False
        
        # Thumbnail
        thumb_frame = ctk.CTkFrame(self, width=80, height=142, fg_color="#252338")
        thumb_frame.grid(row=0, column=0, padx=10, pady=10, sticky="n")
        thumb_frame.grid_propagate(False)
        
        # Try to load cover image using CTkImage
        if story_data.get("cover"):
            try:
                img_path = story_data["cover"]
                pil_image = Image.open(img_path).convert("RGB")
                
                # Use CTkImage for proper scaling
                ctk_image = ctk.CTkImage(
                    light_image=pil_image,
                    dark_image=pil_image,
                    size=(80, 142)
                )
                
                img_label = ctk.CTkLabel(thumb_frame, image=ctk_image, text="")
                img_label.pack(expand=True)
            except Exception as e:
                logger.debug(f"Failed to load thumbnail: {e}")
                ctk.CTkLabel(thumb_frame, text="📄", font=("Arial", 32)).pack(expand=True)
        else:
            ctk.CTkLabel(thumb_frame, text="📄", font=("Arial", 32)).pack(expand=True)
        
        # Info
        info_frame = ctk.CTkFrame(self, fg_color="transparent")
        info_frame.grid(row=0, column=1, padx=10, pady=10, sticky="ew")
        
        ctk.CTkLabel(
            info_frame, text=story_data["title"], 
            font=("Arial", 14, "bold"), anchor="w"
        ).pack(fill="x", pady=(0, 5))
        
        meta_text = f"📁 {story_data['category']}  •  🌐 {story_data['language']}  •  📄 {story_data['scenes']} scenes"
        ctk.CTkLabel(
            info_frame, text=meta_text,
            font=("Arial", 10), text_color="#a8a8b8", anchor="w"
        ).pack(fill="x")
        
        self.grid_columnconfigure(1, weight=1)
        
        # Click handler
        self.bind("<Button-1>", lambda e: self._on_click())
        for widget in self.winfo_children():
            widget.bind("<Button-1>", lambda e: self._on_click())
            for child in widget.winfo_children():
                child.bind("<Button-1>", lambda e: self._on_click())
    
    def _on_click(self):
        self.on_select(self.story_data)
    
    def set_selected(self, selected: bool):
        self.selected = selected
        if selected:
            self.configure(border_color="#5b3ee0")
        else:
            self.configure(border_color="#3a3850")


class JobCard(ctk.CTkFrame):
    """A card widget representing a video generation job."""
    
    def __init__(self, parent, job_id: str, story_name: str):
        super().__init__(parent, fg_color="#1a1828", corner_radius=10)
        
        self.job_id = job_id
        
        # Header
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.pack(fill="x", padx=15, pady=(15, 5))
        
        self.title_label = ctk.CTkLabel(
            header_frame, text=story_name,
            font=("Arial", 13, "bold"), anchor="w"
        )
        self.title_label.pack(side="left", fill="x", expand=True)
        
        self.status_label = ctk.CTkLabel(
            header_frame, text="QUEUED",
            font=("Arial", 10, "bold"), fg_color="#3a3850",
            corner_radius=12, padx=12, pady=4
        )
        self.status_label.pack(side="right")
        
        # Stage
        self.stage_label = ctk.CTkLabel(
            self, text="Initializing...",
            font=("Arial", 10), text_color="#a8a8b8", anchor="w"
        )
        self.stage_label.pack(fill="x", padx=15, pady=5)
        
        # Progress bar
        self.progress_bar = ctk.CTkProgressBar(self, height=6)
        self.progress_bar.pack(fill="x", padx=15, pady=(5, 10))
        self.progress_bar.set(0)
        
        # Actions frame (hidden initially)
        self.actions_frame = ctk.CTkFrame(self, fg_color="transparent")
        
        self.play_btn = ctk.CTkButton(
            self.actions_frame, text="▶️ Play", width=100, height=30,
            command=self._on_play
        )
        self.play_btn.pack(side="left", padx=5)
        
        self.open_folder_btn = ctk.CTkButton(
            self.actions_frame, text="📁 Open Folder", width=120, height=30,
            command=self._on_open_folder
        )
        self.open_folder_btn.pack(side="left", padx=5)
        
        self.output_path: Optional[Path] = None
    
    def update_progress(self, progress: int, stage: str, status: str):
        self.progress_bar.set(progress / 100.0)
        self.stage_label.configure(text=stage)
        
        # Update status color
        status_colors = {
            "queued": "#3a3850",
            "processing": "#f39c12",
            "complete": "#2ecc71",
            "failed": "#e74c3c",
        }
        self.status_label.configure(
            text=status.upper(),
            fg_color=status_colors.get(status.lower(), "#3a3850")
        )
        
        # Show actions if complete
        if status.lower() == "complete":
            self.actions_frame.pack(fill="x", padx=15, pady=(5, 15))
    
    def set_output_path(self, path: Path):
        self.output_path = path
    
    def set_error(self, error: str):
        self.stage_label.configure(text=f"❌ Error: {error}", text_color="#e74c3c")
    
    def _on_play(self):
        if self.output_path and self.output_path.exists():
            os.startfile(str(self.output_path))
    
    def _on_open_folder(self):
        if self.output_path and self.output_path.exists():
            os.startfile(str(self.output_path.parent))


class StoryVideoGeneratorGUI(ctk.CTk):
    """Main application window."""
    
    def __init__(self):
        super().__init__()
        
        self.title("Story Video Generator")
        self.geometry("1400x800")
        
        # State
        self.stories: list[dict] = []
        self.selected_story: Optional[dict] = None
        self.story_cards: list[StoryCard] = []
        self.job_cards: Dict[str, JobCard] = {}
        self.jobs_queue: Queue = Queue()
        
        # Load config
        try:
            self.config = load_config(BASE_DIR / "config.yaml")
        except Exception as e:
            logger.warning(f"Failed to load config: {e}. Using defaults.")
            self.config = AppConfig()
        
        # Presets
        self.presets = {
            "draft": {"name": "⚡ Draft", "desc": "Fast preview (24fps)"},
            "social": {"name": "📱 Social", "desc": "Optimized for social media"},
            "premium": {"name": "✨ Premium", "desc": "Highest quality (60fps)"},
        }
        self.selected_preset = ctk.StringVar(value="draft")
        
        # Voices
        self.voices = {
            "hannah": "Hannah (F) - Clear, friendly",
            "diana": "Diana (F) - Warm, narrative",
            "autumn": "Autumn (F) - Soft, calming",
            "austin": "Austin (M) - Deep, authoritative",
            "daniel": "Daniel (M) - Natural, conversational",
            "troy": "Troy (M) - Energetic, upbeat",
        }
        self.selected_voice = ctk.StringVar(value="hannah")
        
        self.music_path: Optional[Path] = None
        
        self._build_ui()
        self._load_stories()
        
        # Start job processor thread
        self.processing_thread = threading.Thread(target=self._process_jobs, daemon=True)
        self.processing_thread.start()
    
    def _build_ui(self):
        """Build the user interface."""
        
        # Header
        header = ctk.CTkFrame(self, fg_color="#1a1828", height=80)
        header.pack(fill="x", padx=10, pady=(10, 0))
        header.pack_propagate(False)
        
        ctk.CTkLabel(
            header, text="🎬 Story Video Generator",
            font=("Arial", 24, "bold")
        ).pack(pady=20)
        
        # Main container
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.pack(fill="both", expand=True, padx=10, pady=10)
        
        # Left: Stories
        left_frame = ctk.CTkFrame(main, fg_color="#1a1828", corner_radius=10)
        left_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        
        stories_header = ctk.CTkFrame(left_frame, fg_color="transparent", height=50)
        stories_header.pack(fill="x", padx=15, pady=10)
        stories_header.pack_propagate(False)
        
        ctk.CTkLabel(
            stories_header, text="📚 Stories",
            font=("Arial", 16, "bold"), anchor="w"
        ).pack(side="left", fill="x", expand=True)
        
        ctk.CTkButton(
            stories_header, text="🔄", width=40, height=30,
            command=self._load_stories
        ).pack(side="right", padx=5)
        
        ctk.CTkButton(
            stories_header, text="📁", width=40, height=30,
            command=self._browse_folder
        ).pack(side="right")
        
        # Search
        self.search_var = ctk.StringVar()
        self.search_var.trace("w", lambda *args: self._filter_stories())
        
        search_entry = ctk.CTkEntry(
            left_frame, placeholder_text="Search stories...",
            textvariable=self.search_var
        )
        search_entry.pack(fill="x", padx=15, pady=(0, 10))
        
        # Stories list
        self.stories_scroll = ctk.CTkScrollableFrame(left_frame, fg_color="transparent")
        self.stories_scroll.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        
        # Center: Configuration
        center_frame = ctk.CTkFrame(main, fg_color="#1a1828", corner_radius=10)
        center_frame.grid(row=0, column=1, sticky="nsew", padx=5)
        
        ctk.CTkLabel(
            center_frame, text="⚙️ Configuration",
            font=("Arial", 16, "bold"), anchor="w"
        ).pack(fill="x", padx=15, pady=15)
        
        # Selected story
        ctk.CTkLabel(
            center_frame, text="Selected Story",
            font=("Arial", 11), text_color="#a8a8b8", anchor="w"
        ).pack(fill="x", padx=15, pady=(10, 5))
        
        self.selected_story_label = ctk.CTkLabel(
            center_frame, text="No story selected",
            font=("Arial", 12), anchor="w",
            fg_color="#0f0e1a", corner_radius=8, height=60, padx=15
        )
        self.selected_story_label.pack(fill="x", padx=15, pady=(0, 15))
        
        # Preset
        ctk.CTkLabel(
            center_frame, text="Render Preset",
            font=("Arial", 11), text_color="#a8a8b8", anchor="w"
        ).pack(fill="x", padx=15, pady=(10, 5))
        
        preset_frame = ctk.CTkFrame(center_frame, fg_color="transparent")
        preset_frame.pack(fill="x", padx=15, pady=(0, 15))
        
        for idx, (key, preset) in enumerate(self.presets.items()):
            rb = ctk.CTkRadioButton(
                preset_frame, text=f"{preset['name']}\n{preset['desc']}",
                variable=self.selected_preset, value=key,
                font=("Arial", 10)
            )
            rb.pack(fill="x", pady=5)
        
        # Voice
        ctk.CTkLabel(
            center_frame, text="Voice",
            font=("Arial", 11), text_color="#a8a8b8", anchor="w"
        ).pack(fill="x", padx=15, pady=(10, 5))
        
        voice_menu = ctk.CTkOptionMenu(
            center_frame, variable=self.selected_voice,
            values=list(self.voices.keys()),
            command=self._update_voice_label
        )
        voice_menu.pack(fill="x", padx=15, pady=(0, 5))
        
        self.voice_desc_label = ctk.CTkLabel(
            center_frame, text=self.voices["hannah"],
            font=("Arial", 9), text_color="#a8a8b8", anchor="w"
        )
        self.voice_desc_label.pack(fill="x", padx=15, pady=(0, 15))
        
        # Music
        ctk.CTkLabel(
            center_frame, text="Background Music (optional)",
            font=("Arial", 11), text_color="#a8a8b8", anchor="w"
        ).pack(fill="x", padx=15, pady=(10, 5))
        
        music_frame = ctk.CTkFrame(center_frame, fg_color="transparent")
        music_frame.pack(fill="x", padx=15, pady=(0, 15))
        
        self.music_label = ctk.CTkLabel(
            music_frame, text="No music selected",
            font=("Arial", 10), anchor="w"
        )
        self.music_label.pack(side="left", fill="x", expand=True)
        
        ctk.CTkButton(
            music_frame, text="Browse", width=80,
            command=self._browse_music
        ).pack(side="right", padx=(10, 0))
        
        # Generate button
        self.generate_btn = ctk.CTkButton(
            center_frame, text="▶️ Generate Video",
            font=("Arial", 14, "bold"), height=50,
            state="disabled", command=self._generate_video
        )
        self.generate_btn.pack(fill="x", padx=15, pady=20)
        
        # Right: Jobs
        right_frame = ctk.CTkFrame(main, fg_color="#1a1828", corner_radius=10)
        right_frame.grid(row=0, column=2, sticky="nsew", padx=(5, 0))
        
        jobs_header = ctk.CTkFrame(right_frame, fg_color="transparent", height=50)
        jobs_header.pack(fill="x", padx=15, pady=10)
        jobs_header.pack_propagate(False)
        
        ctk.CTkLabel(
            jobs_header, text="📊 Render Queue",
            font=("Arial", 16, "bold"), anchor="w"
        ).pack(side="left", fill="x", expand=True)
        
        ctk.CTkButton(
            jobs_header, text="🗑️", width=40, height=30,
            command=self._clear_completed
        ).pack(side="right")
        
        # Jobs list
        self.jobs_scroll = ctk.CTkScrollableFrame(right_frame, fg_color="transparent")
        self.jobs_scroll.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        
        self.empty_jobs_label = ctk.CTkLabel(
            self.jobs_scroll, text="No renders yet\n\nSelect a story and click\n'Generate Video' to start",
            font=("Arial", 11), text_color="#a8a8b8"
        )
        self.empty_jobs_label.pack(pady=60)
        
        # Configure grid
        main.grid_columnconfigure(0, weight=1)
        main.grid_columnconfigure(1, weight=1)
        main.grid_columnconfigure(2, weight=1)
        main.grid_rowconfigure(0, weight=1)
    
def _load_stories(self):
    """Load stories from the default directory."""
    self.stories.clear()
    for card in self.story_cards:
        card.destroy()
    self.story_cards.clear()
    
    if not DEFAULT_STORY_PATH.exists():
        ctk.CTkLabel(
            self.stories_scroll,
            text=f"Story directory not found:\n{DEFAULT_STORY_PATH}\n\nClick 📁 to browse",
            font=("Arial", 11), text_color="#a8a8b8"
        ).pack(pady=40)
        return
    
    # Find all story.json files
    for item in DEFAULT_STORY_PATH.iterdir():
        if item.is_dir():
            story_json = item / "story.json"
            if story_json.exists():
                try:
                    with open(story_json, encoding='utf-8') as f:
                        data = json.load(f)
                    
                    # Find cover image - resolve relative paths
                    cover_path = None
                    
                    # First try the coverImageUrl from JSON (if it exists)
                    if data.get("coverImageUrl"):
                        # coverImageUrl is relative to the story directory
                        cover_candidate = item / data["coverImageUrl"]
                        logger.debug(f"Trying cover path: {cover_candidate}")
                        if cover_candidate.exists():
                            cover_path = cover_candidate
                            logger.info(f"Found cover image: {cover_path}")
                        else:
                            logger.warning(f"Cover image not found at: {cover_candidate}")
                    
                    # Otherwise try common filenames in assets/images
                    if not cover_path:
                        assets_dir = item / "assets" / "images"
                        if assets_dir.exists():
                            for candidate in ["cover.png", "cover.jpg", "scene1.png", "scene1.jpg"]:
                                candidate_path = assets_dir / candidate
                                if candidate_path.exists():
                                    cover_path = candidate_path
                                    logger.info(f"Found fallback cover: {cover_path}")
                                    break
                    
                    if not cover_path:
                        logger.warning(f"No cover image found for story: {data.get('title')}")
                    
                    story_info = {
                        "id": data.get("id", item.name),
                        "title": data.get("title", item.name),
                        "category": data.get("category", "general"),
                        "language": data.get("language", "en-US"),
                        "scenes": len(data.get("scenes", [])),
                        "path": item,
                        "cover": str(cover_path.resolve()) if cover_path else None,  # Convert to absolute path string
                    }
                    self.stories.append(story_info)
                except Exception as e:
                    logger.warning(f"Failed to load {story_json}: {e}")
    
    self.stories.sort(key=lambda s: s["title"])
    self._render_stories()
        
    def _render_stories(self):
        """Render story cards."""
        search_term = self.search_var.get().lower()
        
        for card in self.story_cards:
            card.destroy()
        self.story_cards.clear()
        
        filtered = [
            s for s in self.stories
            if search_term in s["title"].lower() or search_term in s["category"].lower()
        ]
        
        if not filtered:
            ctk.CTkLabel(
                self.stories_scroll,
                text="No stories found",
                font=("Arial", 11), text_color="#a8a8b8"
            ).pack(pady=40)
            return
        
        for story in filtered:
            card = StoryCard(self.stories_scroll, story, self._select_story)
            card.pack(fill="x", pady=5)
            self.story_cards.append(card)
            
            if self.selected_story and story["id"] == self.selected_story["id"]:
                card.set_selected(True)
    
    def _filter_stories(self):
        """Filter stories based on search."""
        self._render_stories()
    
    def _select_story(self, story: dict):
        """Select a story."""
        self.selected_story = story
        
        # Update UI
        for card in self.story_cards:
            card.set_selected(card.story_data["id"] == story["id"])
        
        self.selected_story_label.configure(
            text=f"{story['title']}\n📁 {story['category']}  •  🌐 {story['language']}  •  📄 {story['scenes']} scenes"
        )
        
        self.generate_btn.configure(state="normal")
    
    def _browse_folder(self):
        """Browse for a story directory."""
        folder = filedialog.askdirectory(title="Select Story Directory")
        if folder:
            global DEFAULT_STORY_PATH
            DEFAULT_STORY_PATH = Path(folder)
            self._load_stories()
    
    def _browse_music(self):
        """Browse for background music."""
        file = filedialog.askopenfilename(
            title="Select Background Music",
            filetypes=[("Audio Files", "*.mp3 *.wav *.m4a"), ("All Files", "*.*")]
        )
        if file:
            self.music_path = Path(file)
            self.music_label.configure(text=self.music_path.name)
    
    def _update_voice_label(self, voice: str):
        """Update voice description."""
        self.voice_desc_label.configure(text=self.voices.get(voice, ""))
    
    def _generate_video(self):
        """Queue a video generation job."""
        if not self.selected_story:
            return
        
        job_id = f"job_{int(time.time())}"
        
        # Create job card
        job_card = JobCard(self.jobs_scroll, job_id, self.selected_story["title"])
        job_card.pack(fill="x", pady=5)
        self.job_cards[job_id] = job_card
        
        # Hide empty label
        self.empty_jobs_label.pack_forget()
        
        # Queue job
        job_data = {
            "job_id": job_id,
            "story": self.selected_story,
            "preset": self.selected_preset.get(),
            "voice": self.selected_voice.get(),
            "music": self.music_path,
        }
        self.jobs_queue.put(job_data)
        
        messagebox.showinfo("Job Queued", f"Video generation started for:\n{self.selected_story['title']}")
    
    def _process_jobs(self):
        """Background thread to process video generation jobs."""
        while True:
            try:
                job_data = self.jobs_queue.get()
                self._run_generation(job_data)
            except Exception as e:
                logger.exception("Job processing failed")
    
    def _run_generation(self, job_data: dict):
        """Run video generation for a job."""
        job_id = job_data["job_id"]
        story = job_data["story"]
        preset = job_data["preset"]
        voice = job_data["voice"]
        music = job_data["music"]
        
        def update_progress(progress: int, stage: str, status: str = "processing"):
            if job_id in self.job_cards:
                self.after(0, lambda: self.job_cards[job_id].update_progress(progress, stage, status))
        
        try:
            update_progress(0, "Loading configuration", "processing")
            
            # Load config
            cfg = self.config
            
            # Apply preset
            if preset != "draft":
                presets_path = BASE_DIR / "presets.yaml"
                if presets_path.exists():
                    import yaml
                    with open(presets_path, encoding='utf-8') as f:
                        presets = yaml.safe_load(f) or {}
                    if preset in presets:
                        # Deep merge preset into config
                        preset_cfg = presets[preset]
                        for key, value in preset_cfg.items():
                            if hasattr(cfg, key) and isinstance(value, dict):
                                # Merge nested dicts
                                current = getattr(cfg, key)
                                for subkey, subvalue in value.items():
                                    if hasattr(current, subkey):
                                        setattr(current, subkey, subvalue)
                            elif hasattr(cfg, key):
                                setattr(cfg, key, value)
            
            update_progress(5, "Loading story")
            
            # Load story
            story_path = story["path"] / "story.json"
            story_data = load_story(story_path)
            
            # Resolve local image paths (THIS IS THE KEY FIX)
            story_data = resolve_local_image_paths(story_data, story["path"])
            
            update_progress(10, "Resolving images")
            
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
                update_progress(progress, f"Loading image {idx + 1}/{len(story_data.scenes)}")
            
            # Output path
            output_dir = BASE_DIR / "output"
            output_dir.mkdir(exist_ok=True)
            output_filename = f"{story['path'].name}_{job_id}.mp4"
            output_path = output_dir / output_filename
            
            update_progress(30, "Generating video")
            
            # Progress callback
            def progress_cb(stage: str, cur: int, total: int):
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
                        update_progress(progress, stage)
                        break
            
            # Generate video
            with tempfile.TemporaryDirectory(prefix=f"story_video_{job_id}_") as tmp:
                result_path = assemble_video(
                    story_data,
                    resolved_images,
                    cfg,
                    music,
                    voice,
                    output_path,
                    work_dir=Path(tmp),
                    progress_cb=progress_cb,
                )
            
            update_progress(100, "Complete ✓", "complete")
            
            # Set output path for actions
            if job_id in self.job_cards:
                self.after(0, lambda: self.job_cards[job_id].set_output_path(result_path))
            
            # Show notification
            self.after(0, lambda: messagebox.showinfo(
                "Video Complete",
                f"Video generated successfully:\n{result_path.name}\n\nClick 'Play' or 'Open Folder' in the job card."
            ))
        
        except Exception as e:
            logger.exception(f"Job {job_id} failed")
            update_progress(0, str(e), "failed")
            if job_id in self.job_cards:
                self.after(0, lambda: self.job_cards[job_id].set_error(str(e)))
            
            self.after(0, lambda: messagebox.showerror("Generation Failed", f"Error: {e}"))
    
    def _clear_completed(self):
        """Clear completed jobs."""
        to_remove = [
            job_id for job_id, card in self.job_cards.items()
            if "complete" in card.status_label.cget("text").lower() or
               "failed" in card.status_label.cget("text").lower()
        ]
        
        for job_id in to_remove:
            self.job_cards[job_id].destroy()
            del self.job_cards[job_id]
        
        if not self.job_cards:
            self.empty_jobs_label.pack(pady=60)


def main():
    """Main entry point."""
    app = StoryVideoGeneratorGUI()
    app.mainloop()


if __name__ == "__main__":
    main()