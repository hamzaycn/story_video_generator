# Story Video Generator

A standalone Python CLI that turns children's-story data (title, category,
language, per-scene text + image) into a single, professional,
social-media-ready vertical MP4 (1080x1920) — narrated, captioned,
Ken-Burns-animated, transitioned, and branded — running entirely on your
machine via `ffmpeg` + `Pillow`. No server, no hosting costs, no moviepy.

## What it produces

- Narrated audio per scene (Groq Orpheus TTS for English; pluggable for
  other languages)
- Ken Burns zoom/pan on each scene image, computed frame-by-frame in
  Pillow (not ffmpeg `zoompan`, which jitters at low frame counts)
- Clean animated captions: real font-metric text wrapping, rounded
  semi-transparent background bar, drop shadow, fade in/out, optional
  word-by-word karaoke highlight, correct script/font selection, and
  proper RTL shaping for Arabic/Hebrew
- Crossfade transitions between scenes (`ffmpeg xfade`/`acrossfade`)
- A branded animated intro/title card and outro/CTA card
- Optional background music, ducked under narration via a real sidechain
  compressor, with a tail fade-out
- A delivery-ready MP4 (H.264, yuv420p, AAC, `+faststart`) **and** a
  standalone `.srt` subtitle file

## Project structure

```
story_video_generator/
  config.yaml            # all tunables: resolution, Ken Burns, captions, brand, audio, output
  generate_video.py      # CLI entry point (typer)
  story.json              # sample story
  requirements.txt
  .env.example
  core/
    story_loader.py       # pydantic schema + validation
    config.py              # AppConfig (config.yaml schema)
    cache.py                # content-addressed image/audio cache
    image_manager.py        # download/cache/placeholder-fallback for images
    ken_burns.py             # pan/zoom math (pure, unit-tested) + frame animator
    subtitles.py             # caption rendering, font/script mapping, RTL, SRT export
    video_builder.py         # ffmpeg orchestration: scenes, intro/outro, transitions, mux
    tts/
      base.py                 # TTSProvider interface
      groq_provider.py         # Groq Orpheus (English)
      google_provider.py       # stub for other languages
      factory.py                # language -> provider routing
  assets/
    fonts/                 # per-script font files (see assets/fonts/README.md)
    branding/               # logo, QR/app-badge image
    music/                   # royalty-free background tracks
  cache/                  # downloaded images + generated audio (gitignored)
  output/                 # rendered videos + .srt (gitignored)
  tests/
    test_core_logic.py     # unit tests for pure math (Ken Burns, timing, SRT)
```

## Setup

### 1. System dependencies

- **Python 3.11+**
- **ffmpeg** on PATH (needs `libx264`, `aac`, and the `sidechaincompress`/
  `xfade`/`acrossfade` filters — present in any normal full/non-minimal
  ffmpeg build):
  - macOS: `brew install ffmpeg`
  - Ubuntu/Debian: `sudo apt-get install ffmpeg`
  - Windows: https://www.gyan.dev/ffmpeg/builds/ (add `bin/` to PATH)

### 2. Python environment

```bash
cd story_video_generator
python3.11 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Pillow + libraqm/HarfBuzz (important for non-Latin scripts)

Correct text shaping for Arabic, Devanagari, Thai, CJK, etc. requires
Pillow's **Raqm** layout engine (HarfBuzz + FriBiDi + FreeType under the
hood). Two things need to be true:

1. Pillow must be built with Raqm support — **official PyPI wheels for
   Pillow >= 8.2.0 already bundle a modified libraqm**, so a plain
   `pip install Pillow` is usually enough for the Raqm *code* to be present.
2. That bundled libraqm loads **FriBiDi at runtime**, and FriBiDi is *not*
   bundled — it must be installed separately as a system library, or Raqm
   silently falls back to basic layout (fine for Latin text, broken
   shaping for Arabic/Devanagari/Thai/etc.).

Install FriBiDi and verify:

```bash
# macOS
brew install fribidi

# Ubuntu/Debian
sudo apt-get install libfribidi0    # usually already present; libfribidi-dev if building from source

# Verify Raqm is actually active:
python -c "from PIL import features; print(features.check('raqm'))"
# -> should print True
```

If it still prints `False` (common with some Homebrew Python builds or
minimal/slim Docker images), rebuild Pillow from source against your
system's libraqm/harfbuzz/fribidi:

```bash
# macOS
brew install freetype harfbuzz fribidi libraqm
pip install --no-binary :all: --force-reinstall Pillow

# Ubuntu/Debian
sudo apt-get install libfreetype6-dev libharfbuzz-dev libfribidi-dev libraqm-dev
pip install --no-binary :all: --force-reinstall Pillow
```

`core/subtitles.py` degrades gracefully either way: if Raqm truly isn't
available, it logs a warning and falls back to Pillow's basic layout
engine rather than crashing — Latin captions look identical either way;
complex scripts lose ligatures/joining until Raqm is active.

### 4. API keys

Copy `.env.example` to `.env` and fill in your Groq key (never hardcode
keys in code or commit `.env`):

```bash
cp .env.example .env
```

```
GROQ_API_KEY=gsk_...
```

Get a free key at https://console.groq.com/keys, and accept the model
terms once at
https://console.groq.com/playground?model=canopylabs/orpheus-v1-english
(required before the API will serve English TTS requests).

### 5. Fonts and branding

See `assets/fonts/README.md` for the exact per-script font filenames
expected by default (all Google Noto, OFL-licensed, safe to bundle) and
how to override them in `config.yaml`. Drop your logo at
`assets/branding/logo.png` (and optionally a QR/app-badge image).

## Usage

```bash
python generate_video.py \
  --story story.json \
  --output output/story_123.mp4 \
  --music assets/music/calm_piano.mp3 \
  --voice hannah \
  --config config.yaml
```

Dry run (validates story JSON, resolves fonts/images, estimates duration —
no rendering, ffmpeg calls, or TTS API calls):

```bash
python generate_video.py --story story.json --output output/story_123.mp4 --dry-run
```

Run unit tests (pure logic only — no ffmpeg/network required):

```bash
pip install pytest
pytest tests/
```

## Languages & TTS routing

`core/tts/factory.py` routes by `story.language` (BCP-47):

- `en*` → `GroqTTSProvider` (`canopylabs/orpheus-v1-english`). Available
  voices at time of writing: `autumn`, `diana`, `hannah`, `austin`,
  `daniel`, `troy`.
- Everything else → `GoogleCloudTTSProvider`, currently a **stub** that
  raises a clear, actionable `TTSRequestError` telling you what to
  implement (see the class docstring in `core/tts/google_provider.py` for
  a ready-to-adapt code sketch using `google-cloud-texttospeech`). This is
  intentional: silently mis-narrating a non-English story with an
  English-only model is worse than a loud, early failure.

Groq also hosts `canopylabs/orpheus-arabic-saudi` for Saudi Arabic — if
you want to use it, instantiate `GroqTTSProvider(model="canopylabs/orpheus-arabic-saudi")`
and register it for `"ar"` in `factory.py`.

## Key design notes

- **Caching**: images are cached by URL/path hash; narration audio is
  cached by `hash(text, voice, model, language)`. Editing scene text or
  switching voice/model invalidates only the affected clips — reruns of
  an unchanged story make zero network/API calls.
- **Scene timing**: each scene's on-screen duration is driven by the
  *actual* narration length (`soundfile`), not a guess, with a
  configurable enforced minimum (`timing.min_scene_duration_s`, default
  3s) so short lines don't produce jarring cuts — the narration is padded
  with trailing silence to match.
- **Ken Burns**: implemented via direct Pillow frame generation (crop +
  resize per output frame, cubic ease-in-out interpolation), not ffmpeg's
  `zoompan`. Source images are pre-upscaled enough that even the maximum
  configured zoom never upsamples past native resolution. Zoom direction
  and one of 6 pan directions rotate deterministically per scene index so
  a multi-scene story never feels repetitive.
- **Captions**: wrapped using real Pillow font-metric measurements (never
  character counts), rendered with a soft rounded background bar + drop
  shadow, fading in/out over a few frames, staying clear of a ~200px top /
  260px bottom safe area to avoid TikTok/Reels/Shorts UI chrome. Optional
  word-by-word karaoke highlight (`captions.karaoke: true`) approximates
  per-word timing by distributing the known scene duration proportionally
  to word character length, since Groq's TTS response has no word
  timestamps — this is clearly an approximation, hence off by default.
- **Transitions**: `ffmpeg xfade` for video and `acrossfade` for audio,
  chained sequentially across intro + all scenes + outro at matching
  offsets/duration (`timing.transition_duration_s`, default 0.5s).
- **Background music**: looped/trimmed to total duration and ducked under
  narration with `sidechaincompress` (narration is the sidechain trigger),
  which reacts naturally to pauses between sentences rather than using
  fixed on/off volume windows, then faded out over the last couple of
  seconds.
- **Graceful degradation**: a failed image download/local path logs a
  warning and substitutes a branded placeholder instead of aborting the
  whole run; TTS/language configuration errors, by contrast, fail loudly
  and immediately (see "Languages & TTS routing" above) because silent
  mis-narration is worse than stopping.
- **Testability**: all pure math (Ken Burns interpolation, easing, word
  timing distribution, SRT timestamp formatting, script/RTL detection) is
  isolated from I/O and covered in `tests/test_core_logic.py`.

## Extending

- **New TTS language**: implement a `TTSProvider` in `core/tts/`, then add
  a routing rule in `core/tts/factory.py`.
- **New script/font**: add an entry to `LANGUAGE_SCRIPT_MAP` and
  `DEFAULT_SCRIPT_FONTS` in `core/subtitles.py`, or override via
  `config.yaml`'s `fonts.script_map`.
- **New transition style**: `xfade` supports many transition names
  (`fade`, `wipeleft`, `circleopen`, `dissolve`, ...) — change the
  hardcoded `transition=fade` in `video_builder.xfade_concat_videos` or
  expose it as a config option.
- **Performance**: each scene clip is currently double-encoded (once when
  rendered, once during final mux). For faster iteration during
  development, you can render scene clips at a lower CRF/preset via
  `config.yaml`'s `output` section, or cache intermediate scene clips by
  content hash the same way images/audio are cached.
