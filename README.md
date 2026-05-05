# UI Visual Tester

Automated visual QA tool that compares live browser UI against Figma designs using pixel diffing and AI-powered analysis (Gemini).

## What It Does

1. **Downloads Figma frames** — fetches design specs directly from the Figma API
2. **Captures live screenshots** — launches a Chrome browser with the target extension loaded (via Patchright) and captures UI states at various viewports
3. **Pixel diff** — compares screenshots against Figma frames at the pixel level
4. **AI analysis** — sends screenshots + Figma data to Gemini for intelligent style and layout comparison
5. **Generates reports** — outputs structured JSON reports with PASS/FAIL verdicts per element

## Test Modes

| Mode | Description |
|------|-------------|
| **Responsive** | Captures the UI at multiple viewport widths and compares layout against Figma |
| **Style** | Extracts DOM computed styles and compares colors, typography, borders, spacing etc. against Figma design tokens |


## Setup

```bash
pip install -r ui_visual_tester/requirements.txt
patchright install chromium
```

Create `ui_visual_tester/.env` (see `.env.example`):
```
FIGMA_TOKEN=...
FIGMA_FILE_KEY_TOPBAR=...
GEMINI_API_KEY=...
```

## Usage

```bash
cd ui_visual_tester

# Full run (responsive + style)
python main.py --product topbar

# Style checks only, specific platform
python main.py --product topbar --platform ecko --only style

# Use a local extension (skip S3/ECS)
python main.py --product topbar --extension-path ./path/to/extension

# Skip ECS build (download from S3 only)
python main.py --product topbar --no-build
```

