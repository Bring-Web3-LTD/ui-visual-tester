import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from the same directory as this config file
_config_dir = Path(__file__).parent
load_dotenv(_config_dir / ".env", encoding="utf-8-sig")

# ── API Keys ──────────────────────────────────────────────
FIGMA_TOKEN       = os.getenv("FIGMA_TOKEN", "")
FIGMA_FILES = {
    "topbar": os.getenv("FIGMA_FILE_KEY_TOPBAR", ""),
}
GEMINI_API_KEY    = os.getenv("GEMINI_API_KEY", "")

# ── Paths ─────────────────────────────────────────────────
PROJECT_DIR      = Path(__file__).parent
SCREENSHOTS_DIR  = PROJECT_DIR / "screenshots"
FIGMA_FRAMES_DIR = PROJECT_DIR / "figma_frames"
REPORTS_DIR      = PROJECT_DIR / "reports"
DIFFS_DIR        = PROJECT_DIR / "diffs"
EXTENSIONS_DIR   = PROJECT_DIR / "extensions"   

# ── Playwright profile ───────────────────────────────────
PROFILE_DIR = "C:/temp/playwright_profile"

# ── AWS / ECS ─────────────────────────────────────────────
AWS_REGION            = os.getenv("AWS_REGION", "eu-central-1")
ECS_CLUSTER           = os.getenv("ECS_CLUSTER", "")         
ECS_TASK_DEFINITION   = os.getenv("ECS_TASK_DEFINITION", "") 
ECS_SUBNETS           = os.getenv("ECS_SUBNETS", "").split(",") 
ECS_SECURITY_GROUPS   = os.getenv("ECS_SECURITY_GROUPS", "").split(",") 
ECS_CONTAINER_NAME    = os.getenv("ECS_CONTAINER_NAME", "dev-env-deployer")

# ── ECS devEnvDeployer defaults ───────────────────────────
DEFAULT_BRANCH          = os.getenv("DEFAULT_BRANCH", "main")
DEFAULT_GITHUB_REPO     = os.getenv("DEFAULT_GITHUB_REPO", "Bring-Web3-LTD/bringweb3")
DEFAULT_FRONTEND_BRANCH = os.getenv("DEFAULT_FRONTEND_BRANCH", "main")
DEFAULT_FRONTEND_REPO   = os.getenv("DEFAULT_FRONTEND_REPO", "Bring-Web3-LTD/chromeExtension")
DESTROY_AFTER_HOURS     = os.getenv("DESTROY_AFTER_HOURS", "10")

# ── S3 extension download ────────────────────────────────
S3_EXTENSIONS_BUCKET = os.getenv("S3_EXTENSIONS_BUCKET", "bring-popup-iframe-tests")

# ── ECS polling ───────────────────────────────────────────
ECS_POLL_INTERVAL    = 30     # seconds between status checks
ECS_POLL_TIMEOUT     = 1800   # max wait = 30 minutes

# ── Timings (ms) ──────────────────────────────────────────
WAIT_AFTER_SEARCH    = 4000
TOPBAR_TIMEOUT       = 30000
WAIT_TOPBAR_RENDER   = 3000

# ── AI Vision model ───────────────────────────────────────
GEMINI_MODEL = "gemini-2.5-flash"
