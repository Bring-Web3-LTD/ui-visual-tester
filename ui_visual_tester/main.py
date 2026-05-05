import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import argparse
import importlib
import shutil
import time
from pathlib import Path
from patchright.sync_api import sync_playwright
from config import (
    FIGMA_FRAMES_DIR,
    REPORTS_DIR, SCREENSHOTS_DIR, DIFFS_DIR,
    TOPBAR_TIMEOUT, WAIT_TOPBAR_RENDER,
)
from core.browser import (
    launch_stealth_context, search_and_wait_for_ui, clean_extension_state,
    perform_state_actions, acquire_iframe, take_topbar_screenshot,
    extract_dom_styles,
)
from core.figma import (
    download_figma_frames, parse_figma_frames,
    extract_all_figma_elements, deduplicate_figma_elements,
)
from core.compare import pixel_diff, claude_compare, claude_compare_style
from core.report import generate_report
from core.deployer import build_extension, download_latest_extension


# ── Extension resolution ──────────────────────────────────
def _wait_for_environment(platform: str, seconds: int = 240):
    print(f"\n  Waiting {seconds // 60} minutes for '{platform}' environment to stabilize...")
    for remaining in range(seconds, 0, -30):
        print(f"    {remaining}s remaining...")
        time.sleep(30)
    print("  Environment ready!")


def resolve_extension(product: str, platform: str, platform_cfg: dict,
                      branch: str = None, frontend_branch: str = None,
                      force_build: bool = False,
                      built_extensions: dict = None,
                      ext_path_override: Path = None) -> Path:

    if ext_path_override:
        return ext_path_override

    built_extensions = built_extensions or {}
    if platform in built_extensions:
        print(f"  Reusing already-built extension for '{platform}': {built_extensions[platform]}")
        return built_extensions[platform]

    if force_build:
        env_name = f"ui-test-{product}-{platform}"
        ext = build_extension(env_name, platform, platform_cfg, branch, frontend_branch,
                              force_build=True)
        _wait_for_environment(platform)
        return ext

    # Download from S3; fall back to ECS build if not found
    print(f"  Downloading extension for platform '{platform}' from S3...")
    try:
        return download_latest_extension(product, platform)
    except FileNotFoundError:
        print(f"  No extension in S3 for '{platform}', building via ECS...")
        env_name = f"ui-test-{product}-{platform}"
        ext = build_extension(env_name, platform, platform_cfg, branch, frontend_branch,
                              force_build=True)
        _wait_for_environment(platform)
        return ext


# ── Load product config dynamically ──────────────────────
def load_product(product: str):
    return importlib.import_module(f"products.{product}")


# ── Step 2a: Capture responsive screenshots ──────────────
def capture_responsive(product: str, ext_path: Path,
                       responsive_frames: list, prod_cfg):
    SCREENSHOTS_DIR.mkdir(exist_ok=True)
    saved = []

    terminal_state = getattr(prod_cfg, 'TERMINAL_STATE', 'success')
    ready_sels = getattr(prod_cfg, 'READY_SELECTORS', None)

    # Terminal states last (they may trigger opt-out)
    responsive_frames = sorted(
        responsive_frames, key=lambda f: (f[2] == terminal_state, f)
    )

    with sync_playwright() as p:
        ctx, page = launch_stealth_context(p, ext_path)

        sel = prod_cfg.SEL
        states = prod_cfg.STATES
        search_query = prod_cfg.SEARCH_QUERY
        browser_h = prod_cfg.DEFAULT_BROWSER_HEIGHT

        for i, (frame_name, vp_width, state_name) in enumerate(responsive_frames):
            print(f"\n>>> Responsive: {frame_name}  (width={vp_width}, state={state_name})")

            page.set_viewport_size({"width": vp_width, "height": browser_h})

            try:
                search_and_wait_for_ui(page, search_query, sel, ready_sels)
            except Exception as e:
                print(f"  SKIP {frame_name}: {e}")
                continue

            actions = states.get(state_name, [])
            frame = acquire_iframe(page, sel)
            if not frame:
                print(f"  SKIP {frame_name}: iframe not found")
                continue

            perform_state_actions(frame, actions)

            file_path = SCREENSHOTS_DIR / f"{frame_name}.png"
            if take_topbar_screenshot(page, sel, file_path):
                saved.append(file_path)

            if state_name == terminal_state and i < len(responsive_frames) - 1:
                print("  Reopening browser after terminal state (extension reset)...")
                ctx.close()
                clean_extension_state()
                ctx, page = launch_stealth_context(p, ext_path)

        ctx.close()

    return saved


def capture_styles(product: str, style_frames: list, prod_cfg,
                   ext_path: Path = None,
                   branch: str = None, frontend_branch: str = None,
                   force_build: bool = False,
                   built_extensions: dict = None):
    SCREENSHOTS_DIR.mkdir(exist_ok=True)
    results = {}
    built_extensions = built_extensions or {}

    sel = prod_cfg.SEL
    states = prod_cfg.STATES
    search_query = prod_cfg.SEARCH_QUERY
    browser_h = prod_cfg.DEFAULT_BROWSER_HEIGHT
    terminal_state = getattr(prod_cfg, 'TERMINAL_STATE', 'success')
    ready_sels = getattr(prod_cfg, 'READY_SELECTORS', None)

    for frame_name, platform in style_frames:
        platform_cfg = prod_cfg.PLATFORMS.get(platform)
        if not platform_cfg:
            print(f"  SKIP style '{frame_name}': platform '{platform}' not in PLATFORMS")
            continue

        this_ext = resolve_extension(
            product, platform, platform_cfg,
            branch=branch, frontend_branch=frontend_branch,
            force_build=force_build, built_extensions=built_extensions,
            ext_path_override=ext_path,
        )

        print(f"\n>>> Style: {frame_name}  (platform={platform})")
        state_shots = []

        clean_extension_state()

        # Terminal state last
        sorted_states = sorted(states.items(), key=lambda s: s[0] == terminal_state)

        # ── For each state: fresh context → real search → capture ──
        with sync_playwright() as p:
            for state_idx, (state_name, actions) in enumerate(sorted_states):
                print(f"\n  >> State: {state_name}")

                clean_extension_state()

                ctx, page = launch_stealth_context(p, this_ext)
                page.set_viewport_size({"width": 1920, "height": browser_h})

                # Real search each time (goto doesn't trigger the extension)
                try:
                    search_and_wait_for_ui(page, search_query, sel, ready_sels)
                except Exception as e:
                    print(f"    SKIP state '{state_name}': search/topbar failed ({e})")
                    ctx.close()
                    continue

                frame = acquire_iframe(page, sel)

                if actions and frame:
                    perform_state_actions(frame, actions)

                    # Re-acquire frame after actions (DOM may have changed)
                    time.sleep(0.5)
                    try:
                        iframe_el = page.query_selector(sel["iframe"])
                        if iframe_el:
                            frame = iframe_el.content_frame() or frame
                    except Exception:
                        pass

                dom_styles = extract_dom_styles(frame) if frame else {}
                if not dom_styles:
                    print(f"    WARNING: DOM extraction returned 0 elements "
                          f"(frame={'found' if frame else 'None'})")

                safe_state = state_name.replace(" ", "_")
                file_path = SCREENSHOTS_DIR / f"style_{frame_name}_{safe_state}.png"
                if take_topbar_screenshot(page, sel, file_path):
                    state_shots.append((state_name, file_path, dom_styles))

                ctx.close()

        results[frame_name] = state_shots

    return results


# ── CLI ──────────────────────────────────────────────────
if __name__ == "__main__":

    parser = argparse.ArgumentParser(description="Visual QA Tool")
    parser.add_argument("--product", default="topbar")
    parser.add_argument("--platform", default=None)
    parser.add_argument("--branch", default=None)
    parser.add_argument("--frontend-branch", default=None)
    parser.add_argument("--extension-path", default=None)
    parser.add_argument("--no-build", action="store_true")
    parser.add_argument("--only", default=None, choices=["responsive", "style"])
    args = parser.parse_args()

    prod_cfg = load_product(args.product)
    platform = args.platform or prod_cfg.DEFAULT_PLATFORM

    # Clean previous outputs
    for folder in [SCREENSHOTS_DIR, DIFFS_DIR, REPORTS_DIR, FIGMA_FRAMES_DIR]:
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)
            print(f"  Cleaned: {folder.name}/")

    # Step 1: Figma
    print("=== Step 1: Download Figma frames ===")
    figma_files, figma_nodes = download_figma_frames(args.product)

    if not figma_files:
        print("No Figma frames found. Exiting.")
        sys.exit(1)

    frame_names = [f.stem for f in figma_files]
    responsive, styles = parse_figma_frames(args.product, frame_names)

    if args.only == "responsive":
        styles = []
    elif args.only == "style":
        responsive = []

    if args.platform and styles:
        styles = [(f, p) for f, p in styles if p == args.platform]

    print(f"\nResponsive frames: {[r[0] for r in responsive]}")
    print(f"Style frames: {[s[0] for s in styles]}")

    # Step 2: Capture
    print("\n=== Step 2: Build & Capture screenshots ===")
    clean_extension_state()
    all_screenshots = []
    ext_path = Path(args.extension_path) if args.extension_path else None
    force_build = not args.no_build

    if not ext_path and responsive:
        platform_cfg = prod_cfg.PLATFORMS.get(platform)
        if not platform_cfg:
            print(f"ERROR: Platform '{platform}' not found in product PLATFORMS")
            sys.exit(1)
        try:
            ext_path = resolve_extension(
                args.product, platform, platform_cfg,
                branch=args.branch, frontend_branch=args.frontend_branch,
                force_build=force_build,
            )
        except FileNotFoundError as e:
            print(f"  {e}")
            print("  No extension in S3. Remove --no-build to build via ECS, "
                  "or use --extension-path for local.")
            sys.exit(1)
    elif ext_path:
        print(f"  Using local extension: {ext_path}")

    if responsive:
        print("\n--- Responsive captures ---")
        shots = capture_responsive(args.product, ext_path, responsive, prod_cfg)
        all_screenshots.extend(shots)

    style_results = {}
    if styles:
        print("\n--- Style captures ---")
        style_ext = Path(args.extension_path) if args.extension_path else None
        already_built = {platform: ext_path} if ext_path and not args.extension_path else {}
        style_results = capture_styles(
            args.product, styles, prod_cfg,
            ext_path=style_ext,
            branch=args.branch, frontend_branch=args.frontend_branch,
            force_build=force_build,
            built_extensions=already_built,
        )

    # Step 3: Compare
    print("\n=== Step 3: Compare with Claude ===")
    results = []

    # 3a: Responsive
    for screenshot_path in all_screenshots:
        name = screenshot_path.stem
        figma_path = FIGMA_FRAMES_DIR / f"{name}.png"
        if not figma_path.exists():
            print(f"  SKIP: No Figma frame for {name}")
            continue

        print(f"\n--- Pixel diff: {name} ---")
        diff_stats = pixel_diff(screenshot_path, figma_path)
        ai_result = claude_compare(screenshot_path, figma_path, diff_stats=diff_stats)

        results.append({
            "name": name,
            "screenshot": screenshot_path.name,
            "figma": figma_path.name,
            "ai_result": ai_result,
            "mode": "responsive",
            "diff_stats": diff_stats,
        })

    # 3b: Style
    if styles:
        for frame_name, platform in styles:
            spec_path = FIGMA_FRAMES_DIR / f"{frame_name}.png"
            state_data = style_results.get(frame_name, [])
            if not spec_path.exists():
                print(f"  SKIP style: No spec sheet for {frame_name}")
                continue
            if not state_data:
                print(f"  SKIP style: No screenshots captured for {frame_name}")
                continue

            spec_node = figma_nodes.get(frame_name, {})
            raw_elements = extract_all_figma_elements(spec_node)
            figma_elements = deduplicate_figma_elements(raw_elements, prod_cfg)
            print(f"\n  Figma elements for {frame_name}: "
                  f"{len(raw_elements)} raw → {len(figma_elements)} unique")

            state_screenshots = []
            all_dom_elements = {}
            for state_name, shot_path, dom_elements in state_data:
                state_screenshots.append(shot_path)
                all_dom_elements[state_name] = dom_elements
                print(f"    {state_name}: {len(dom_elements)} DOM elements")

            print(f"\n--- Style check: {frame_name} ({len(state_screenshots)} states) ---")
            ai_result = claude_compare_style(
                figma_elements, all_dom_elements,
                state_screenshots, spec_path,
            )

            results.append({
                "name": frame_name,
                "screenshot": ", ".join(s.name for s in state_screenshots),
                "figma": spec_path.name,
                "ai_result": ai_result,
                "mode": "style",
                "diff_stats": None,
                "state_shots": [s.name for s in state_screenshots],
                "figma_elements": figma_elements,
                "dom_elements": all_dom_elements,
            })

    # Step 4: Report
    print("\n=== Step 4: Generate report ===")
    if results:
        generate_report(results)
    else:
        print("No comparisons made — check Figma frame names.")

