#!/usr/bin/env python3
"""Setup script to create web interface files."""
from pathlib import Path

BASE_DIR = Path(__file__).parent
WEB_DIR = BASE_DIR / "web"
STATIC_DIR = WEB_DIR / "static"

# Create directories
STATIC_DIR.mkdir(parents=True, exist_ok=True)

# HTML content
HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Story Video Generator</title>
    <link rel="stylesheet" href="/static/style.css">
</head>
<body>
    <div class="container">
        <header>
            <h1>🎬 Story Video Generator</h1>
            <p class="subtitle">Transform your stories into engaging vertical videos</p>
        </header>

        <div class="main-grid">
            <section class="panel story-panel">
                <div class="panel-header">
                    <h2>📚 Select Story</h2>
                    <button id="refreshStories" class="btn-icon" title="Refresh">🔄</button>
                </div>
                
                <div class="search-box">
                    <input type="text" id="storySearch" placeholder="Search stories...">
                </div>

                <div id="storiesList" class="stories-list">
                    <div class="loading">Loading stories...</div>
                </div>

                <div class="upload-section">
                    <label for="fileUpload" class="upload-label">
                        <span>📤 Upload story.json</span>
                        <input type="file" id="fileUpload" accept=".json" hidden>
                    </label>
                </div>
            </section>

            <section class="panel config-panel">
                <h2>⚙️ Configuration</h2>

                <div class="form-group">
                    <label>Selected Story</label>
                    <div id="selectedStoryDisplay" class="selected-story">
                        <span class="placeholder">No story selected</span>
                    </div>
                </div>

                <div class="form-group">
                    <label>Render Preset</label>
                    <div id="presetButtons" class="preset-buttons"></div>
                </div>

                <div class="form-group">
                    <label>Voice</label>
                    <select id="voiceSelect" class="form-control"></select>
                </div>

                <div class="form-group">
                    <label>
                        Background Music (optional)
                        <input type="file" id="musicUpload" accept=".mp3,.wav,.m4a">
                    </label>
                </div>

                <button id="generateBtn" class="btn-primary" disabled>
                    ▶️ Generate Video
                </button>

                <div id="estimatedTime" class="estimated-time" style="display: none;">
                    ⏱️ Estimated time: <span id="estimateValue">~30s</span>
                </div>
            </section>

            <section class="panel jobs-panel">
                <div class="panel-header">
                    <h2>📊 Render Queue</h2>
                    <button id="clearCompleted" class="btn-icon" title="Clear completed">🗑️</button>
                </div>
                
                <div id="jobsList" class="jobs-list">
                    <div class="empty-state">
                        <p>No renders yet</p>
                        <p class="hint">Select a story and click "Generate Video" to start</p>
                    </div>
                </div>
            </section>
        </div>
    </div>

    <script src="/static/app.js"></script>
</body>
</html>
"""

# Minimal CSS (you can expand this)
CSS = """* {
    margin: 0;
    padding: 0;
    box-sizing: border-box;
}

:root {
    --primary: #5b3ee0;
    --bg: #0f0e1a;
    --bg-light: #1a1828;
    --text: #ffffff;
}

body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.6;
}

.container {
    max-width: 1600px;
    margin: 0 auto;
    padding: 20px;
}

/* Add the rest of the CSS from the previous message */
"""

# Minimal JS
JS = """// See full JavaScript code from previous message
console.log('Story Video Generator UI loaded');
"""

# Write files
(STATIC_DIR / "index.html").write_text(HTML, encoding='utf-8')
(STATIC_DIR / "style.css").write_text(CSS, encoding='utf-8')
(STATIC_DIR / "app.js").write_text(JS, encoding='utf-8')

print("✓ Created web/static/index.html")
print("✓ Created web/static/style.css")
print("✓ Created web/static/app.js")
print("\nNow run: python web/app.py")