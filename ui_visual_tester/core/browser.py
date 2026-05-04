import random
import shutil
import time
from pathlib import Path
from config import (
    PROFILE_DIR, TOPBAR_TIMEOUT, WAIT_AFTER_SEARCH, WAIT_TOPBAR_RENDER,
)


# ── Browser launch ────────────────────────────────────────
def launch_stealth_context(playwright, ext_path: Path):
    args = [
        f"--disable-extensions-except={ext_path}",
        f"--load-extension={ext_path}",
        "--disable-ipv6",
    ]

    ctx = playwright.chromium.launch_persistent_context(
        user_data_dir=PROFILE_DIR,
        headless=False,
        args=args,
        no_viewport=True,
    )

    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    return ctx, page

# ── Helpers ───────────────────────────────────────────────
def dismiss_consent(page):
    try:
        consent_btn = page.locator(
            'button:has-text("Accept all"), '
            'button:has-text("Reject all"), '
            'button:has-text("I agree")'
        )
        if consent_btn.first.is_visible(timeout=3000):
            consent_btn.first.click()
            time.sleep(1)
    except Exception:
        pass

def search_and_wait_for_ui(page, search_query: str, sel: dict,
                           ready_selectors: list = None, retries: int = 3):
    for attempt in range(retries):
        print(f"  Search attempt {attempt + 1}/{retries}...")

        page.goto("https://www.google.com", wait_until="domcontentloaded")
        time.sleep(random.uniform(2.5, 4.5))
        dismiss_consent(page)

        try:
            page.mouse.move(random.randint(300, 600), random.randint(200, 400))
            time.sleep(random.uniform(0.3, 0.8))
        except Exception:
            pass

        search_box = page.locator('textarea[name="q"], input[name="q"]:visible').first
        search_box.click()
        time.sleep(random.uniform(0.4, 0.9))

        for char in search_query:
            search_box.type(char, delay=0)
            time.sleep(random.uniform(0.05, 0.25))

        time.sleep(random.uniform(0.8, 1.5))
        page.keyboard.press("Enter")
        time.sleep(WAIT_AFTER_SEARCH / 1000)

        # Check for CAPTCHA
        if "/sorry/" in page.url or "recaptcha" in page.content().lower():
            wait_time = 30 * (attempt + 1)
            print(f"  Google CAPTCHA detected, waiting {wait_time}s...")
            time.sleep(wait_time)
            if attempt < retries - 1:
                continue
            else:
                raise RuntimeError("Google CAPTCHA on all retries")

        try:
            page.wait_for_selector(sel["container"], timeout=TOPBAR_TIMEOUT)
            container = page.query_selector(sel["container"])
            iframe_el = container.query_selector("iframe") if container else None
            if iframe_el and ready_selectors:
                frame = iframe_el.content_frame()
                if frame:
                    ready_css = ", ".join(ready_selectors)
                    frame.wait_for_selector(ready_css, timeout=10000, state="visible")

            time.sleep(WAIT_TOPBAR_RENDER / 1000)
            print(f"  UI visible!")
            return
        except Exception:
            if attempt < retries - 1:
                wait_sec = random.randint(5, 10)
                print(f"  UI not visible, waiting {wait_sec}s before retry...")
                time.sleep(wait_sec)
            else:
                raise

def clean_extension_state():
    profile = Path(PROFILE_DIR)
    ext_dirs = [
        "Default/Local Extension Settings",
        "Default/Extension State",
        "Default/Sync Extension Settings",
        "Default/Managed Extension Settings",
        "Default/Local Storage/leveldb",
    ]
    cleaned = False
    for rel in ext_dirs:
        d = profile / rel
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
            cleaned = True
    if cleaned:
        print("    Cleaned extension state (profile kept)")

# ── Shared capture helpers ────────────────────────────────
def perform_state_actions(frame, actions: list):
    for action_type, value in actions:
        if action_type == "click":
            frame.locator(value).click()
        elif action_type == "hover":
            frame.locator(value).hover()
        elif action_type == "sleep":
            time.sleep(value / 1000)

def acquire_iframe(page, sel: dict, timeout: int = 10000):
    try:
        page.wait_for_selector(sel["iframe"], timeout=timeout, state="attached")
        iframe_el = page.query_selector(sel["iframe"])
        if not iframe_el:
            print("    WARNING: iframe selector matched but query_selector returned None")
            return None
        frame = iframe_el.content_frame()
        if frame:
            frame.wait_for_load_state("domcontentloaded", timeout=5000)
        else:
            print("    WARNING: iframe element found but content_frame() returned None")
        return frame
    except Exception as e:
        print(f"    WARNING: iframe not found: {e}")
        return None

def take_topbar_screenshot(page, sel: dict, file_path: Path) -> bool:
    try:
        topbar = page.locator(sel["container"])
        topbar.screenshot(path=str(file_path), timeout=5000)
        print(f"    Saved: {file_path.name}")
        return True
    except Exception as e:
        print(f"    Screenshot failed for {file_path.stem}: {e}")
        return False

# ── DOM: extract ALL visible elements dynamically ─────────
_DOM_SCAN_JS = """() => {
    const elements = [];
    const MAX_DEPTH = 10;

    const EMPTY = new Set(['', 'rgba(0, 0, 0, 0)', 'transparent']);

    function collectElement(el, path) {
        const tag = el.tagName?.toLowerCase() || '';
        const id = el.id || '';
        const rect = el.getBoundingClientRect();
        if (rect.width < 2 || rect.height < 2) return;

        const s = getComputedStyle(el);
        if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return;

        const bg = s.backgroundColor;
        const color = s.color;
        const hasBg = bg && !EMPTY.has(bg);
        const hasColor = color && !EMPTY.has(color);
        const hasText = el.innerText?.trim().length > 0;
        const isSvg = tag === 'svg' || tag === 'path' || tag === 'use';

        if (hasBg || hasColor || hasText || isSvg || id) {
            const entry = { tag, path };

            if (id) entry.id = id;
            if (el.className && typeof el.className === 'string')
                entry.classes = el.className.split(' ').filter(c => c).slice(0, 5);
            if (hasText) entry.text = el.innerText.trim().substring(0, 80);

            entry.width = Math.round(rect.width);
            entry.height = Math.round(rect.height);

            const styles = {};
            for (let i = 0; i < s.length; i++) {
                const prop = s[i];
                if (prop.startsWith('-')) continue;
                const val = s.getPropertyValue(prop);
                if (EMPTY.has(val)) continue;
                styles[prop] = val;
            }
            entry.styles = styles;

            elements.push(entry);
        }
    }

    function walk(root, path, depth) {
        if (depth > MAX_DEPTH) return;
        for (const el of root.children || root.querySelectorAll?.(':scope > *') || []) {
            const id = el.id ? '#' + el.id : el.tagName?.toLowerCase();
            const childPath = path + '/' + id;
            collectElement(el, childPath);
            walk(el, childPath, depth + 1);
            if (el.shadowRoot) {
                walk(el.shadowRoot, childPath + '/shadow', depth + 1);
            }
        }
    }

    const root = document.body || document.documentElement;
    walk(root, 'root', 0);
    return elements;
}"""

def extract_dom_styles(frame):
    try:
        return frame.evaluate(_DOM_SCAN_JS) or []
    except Exception as e:
        print(f"    DOM scan error: {e}")
        return []
