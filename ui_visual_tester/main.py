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
    PIXEL_DIFF_THRESHOLD,
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

    # Try S3 first (unless force_build)
    if not force_build:
        print(f"  Downloading extension for platform '{platform}' from S3...")
        try:
            return download_latest_extension(platform)
        except FileNotFoundError:
            if not branch and not frontend_branch:
                raise RuntimeError(
                    f"No extension in S3 for '{platform}' and no branch specified. "
                    f"Use --branch/--frontend-branch to build, or --no-build to require S3 only."
                )
            print(f"  No extension in S3 for '{platform}', building via ECS...")

    # Build via ECS (force_build or S3 not found)
    env_name = f"ui-test-{platform}"
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
    """Capture screenshots for style comparison.

    style_frames: list of (frame_name, platform, specific_state_or_None)
        When specific_state is None  → run ALL states (topbar spec-sheet mode)
        When specific_state is given → run only that state (popup per-screen mode)
    """
    SCREENSHOTS_DIR.mkdir(exist_ok=True)
    results = {}
    built_extensions = built_extensions or {}
    resolved_cache = dict(built_extensions)   # local cache for this run

    sel = prod_cfg.SEL
    states = prod_cfg.STATES
    search_query = prod_cfg.SEARCH_QUERY
    browser_h = prod_cfg.DEFAULT_BROWSER_HEIGHT
    terminal_state = getattr(prod_cfg, 'TERMINAL_STATE', 'success')
    ready_sels = getattr(prod_cfg, 'READY_SELECTORS', None)

    for frame_name, platform, specific_state in style_frames:
        platform_cfg = prod_cfg.PLATFORMS.get(platform)
        if not platform_cfg:
            print(f"  SKIP style '{frame_name}': platform '{platform}' not in PLATFORMS")
            continue

        # Resolve extension with caching
        if platform in resolved_cache:
            this_ext = resolved_cache[platform]
        else:
            this_ext = resolve_extension(
                product, platform, platform_cfg,
                branch=branch, frontend_branch=frontend_branch,
                force_build=force_build, built_extensions=resolved_cache,
                ext_path_override=ext_path,
            )
            resolved_cache[platform] = this_ext

        print(f"\n>>> Style: {frame_name}  (platform={platform}"
              f"{', state=' + specific_state if specific_state else ', all states'})")
        state_shots = []

        clean_extension_state()

        # Determine which states to run
        if specific_state is not None:
            actions = states.get(specific_state, [])
            if specific_state not in states:
                print(f"  WARNING: state '{specific_state}' not in STATES dict, using empty actions")
            states_to_run = [(specific_state, actions)]
        else:
            # Terminal state last
            states_to_run = sorted(states.items(), key=lambda s: s[0] == terminal_state)

        # ── For each state: fresh context → real search → capture ──
        with sync_playwright() as p:
            for state_idx, (state_name, actions) in enumerate(states_to_run):
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
                if specific_state is not None:
                    # State already encoded in frame_name
                    file_path = SCREENSHOTS_DIR / f"style_{frame_name}.png"
                else:
                    file_path = SCREENSHOTS_DIR / f"style_{frame_name}_{safe_state}.png"
                if take_topbar_screenshot(page, sel, file_path):
                    state_shots.append((state_name, file_path, dom_styles))

                ctx.close()

        results[frame_name] = state_shots

    return results


# ── Phase functions ───────────────────────────────────────

def phase_figma(product: str, args) -> dict:
    """Phase 1: Download Figma frames. Returns context dict for later phases."""
    prod_cfg = load_product(product)
    product_figma_dir = FIGMA_FRAMES_DIR / product

    cached_frames = list(product_figma_dir.glob("*.png")) if product_figma_dir.exists() else []
    if cached_frames and not args.force_figma:
        print(f"\n  [{product}] Using {len(cached_frames)} cached Figma frames")
        figma_files, figma_nodes = download_figma_frames(product, skip_images=True,
                                                          out_dir=product_figma_dir)
    else:
        print(f"\n  [{product}] Downloading Figma frames...")
        figma_files, figma_nodes = download_figma_frames(product, out_dir=product_figma_dir)

    if not figma_files:
        print(f"  [{product}] No Figma frames found. Skipping.")
        return None

    frame_names = [f.stem for f in figma_files]
    responsive, styles = parse_figma_frames(product, frame_names)

    if args.only == "responsive":
        styles = []
    elif args.only == "style":
        responsive = []

    if args.platform and styles:
        styles = [(f, p, s) for f, p, s in styles if p == args.platform]

    # Sort: DEFAULT_PLATFORM first, then alphabetical
    default_plat = prod_cfg.DEFAULT_PLATFORM.lower()
    styles.sort(key=lambda s: (0 if s[1] == default_plat else 1, s[1], s[0]))

    print(f"  [{product}] Responsive: {len(responsive)} frames, Style: {len(styles)} frames")

    return {
        "product": product,
        "prod_cfg": prod_cfg,
        "figma_dir": product_figma_dir,
        "figma_nodes": figma_nodes,
        "responsive": responsive,
        "styles": styles,
    }


def phase_capture(ctx: dict, args, global_built_extensions: dict) -> dict:
    """Phase 2: Capture screenshots. Returns ctx with capture results added."""
    product = ctx["product"]
    prod_cfg = ctx["prod_cfg"]
    responsive = ctx["responsive"]
    styles = ctx["styles"]
    platform = args.platform or prod_cfg.DEFAULT_PLATFORM

    print(f"\n  [{product}] Capturing screenshots...")
    clean_extension_state()
    all_screenshots = []
    ext_path = Path(args.extension_path) if args.extension_path else None
    force_build = not args.no_build

    if not ext_path and responsive:
        platform_cfg = prod_cfg.PLATFORMS.get(platform)
        if not platform_cfg:
            print(f"  [{product}] ERROR: Platform '{platform}' not found in PLATFORMS")
            ctx["screenshots"] = []
            ctx["style_results"] = {}
            return ctx
        if platform in global_built_extensions:
            ext_path = global_built_extensions[platform]
        else:
            try:
                ext_path = resolve_extension(
                    product, platform, platform_cfg,
                    branch=args.branch, frontend_branch=args.frontend_branch,
                    force_build=force_build,
                )
                global_built_extensions[platform] = ext_path
            except FileNotFoundError as e:
                print(f"    {e}")
                print("    No extension in S3. Remove --no-build to build via ECS, "
                      "or use --extension-path for local.")
                ctx["screenshots"] = []
                ctx["style_results"] = {}
                return ctx
    elif ext_path:
        print(f"    Using local extension: {ext_path}")

    if responsive:
        shots = capture_responsive(product, ext_path, responsive, prod_cfg)
        all_screenshots.extend(shots)

    style_results = {}
    if styles:
        style_ext = Path(args.extension_path) if args.extension_path else None
        style_results = capture_styles(
            product, styles, prod_cfg,
            ext_path=style_ext,
            branch=args.branch, frontend_branch=args.frontend_branch,
            force_build=force_build,
            built_extensions=global_built_extensions,
        )

    ctx["screenshots"] = all_screenshots
    ctx["style_results"] = style_results
    print(f"  [{product}] Captured {len(all_screenshots)} responsive + "
          f"{sum(len(v) for v in style_results.values())} style screenshots")
    return ctx


def phase_compare(ctx: dict, args) -> list:
    """Phase 3: Compare with Claude. Returns list of result dicts."""
    product = ctx["product"]
    prod_cfg = ctx["prod_cfg"]
    figma_dir = ctx["figma_dir"]
    figma_nodes = ctx["figma_nodes"]
    styles = ctx["styles"]
    all_screenshots = ctx["screenshots"]
    style_results = ctx["style_results"]

    results = []

    # Responsive comparisons
    for screenshot_path in all_screenshots:
        name = screenshot_path.stem
        figma_path = figma_dir / f"{name}.png"
        if not figma_path.exists():
            print(f"    SKIP: No Figma frame for {name}")
            continue

        print(f"\n    Pixel diff: {name}")
        diff_stats = pixel_diff(screenshot_path, figma_path)

        if diff_stats["pct"] < args.diff_threshold:
            print(f"      {diff_stats['pct']}% < {args.diff_threshold}% — auto-PASS")
            ai_result = f"AUTO-PASS: pixel diff {diff_stats['pct']}% below threshold {args.diff_threshold}%"
        else:
            ai_result = claude_compare(screenshot_path, figma_path, diff_stats=diff_stats, product_name=product)

        results.append({
            "name": name,
            "product": product,
            "screenshot": screenshot_path.name,
            "figma": figma_path.name,
            "ai_result": ai_result,
            "mode": "responsive",
            "diff_stats": diff_stats,
        })

    # Style comparisons
    for frame_name, plat, _specific_state in styles:
        spec_path = figma_dir / f"{frame_name}.png"
        state_data = style_results.get(frame_name, [])
        if not spec_path.exists():
            print(f"    SKIP style: No spec sheet for {frame_name}")
            continue
        if not state_data:
            print(f"    SKIP style: No screenshots captured for {frame_name}")
            continue

        spec_node = figma_nodes.get(frame_name, {})
        raw_elements = extract_all_figma_elements(spec_node)
        figma_elements = deduplicate_figma_elements(raw_elements, prod_cfg)
        print(f"\n    {frame_name}: {len(raw_elements)} raw → {len(figma_elements)} unique elements")

        state_screenshots = []
        all_dom_elements = {}
        for state_name, shot_path, dom_elements in state_data:
            state_screenshots.append(shot_path)
            all_dom_elements[state_name] = dom_elements

        print(f"    Comparing {len(state_screenshots)} state(s) with Claude...")
        ai_result = claude_compare_style(
            figma_elements, all_dom_elements,
            state_screenshots, spec_path,
            product_name=product,
        )

        results.append({
            "name": frame_name,
            "product": product,
            "screenshot": ", ".join(s.name for s in state_screenshots),
            "figma": spec_path.name,
            "ai_result": ai_result,
            "mode": "style",
            "diff_stats": None,
            "state_shots": [s.name for s in state_screenshots],
            "figma_elements": figma_elements,
            "dom_elements": all_dom_elements,
        })

    return results


# ── Discover available products ───────────────────────────
def discover_products() -> list[str]:
    products_dir = Path(__file__).parent.parent / "products"
    return [
        f.stem for f in products_dir.glob("*.py")
        if f.stem != "__init__" and not f.stem.startswith("_")
    ]


# ── CLI ──────────────────────────────────────────────────
if __name__ == "__main__":

    parser = argparse.ArgumentParser(description="Visual QA Tool")
    parser.add_argument("--product", default="all",
                        help="Product name or 'all' to run all products")
    parser.add_argument("--platform", default=None)
    parser.add_argument("--branch", default=None)
    parser.add_argument("--frontend-branch", default=None)
    parser.add_argument("--extension-path", default=None)
    parser.add_argument("--no-build", action="store_true")
    parser.add_argument("--only", default=None, choices=["responsive", "style"])
    parser.add_argument("--force-figma", action="store_true",
                        help="Re-download Figma frames even if cached")
    parser.add_argument("--diff-threshold", type=float, default=PIXEL_DIFF_THRESHOLD,
                        help=f"Skip AI when pixel diff < this %% (default: {PIXEL_DIFF_THRESHOLD})")
    args = parser.parse_args()

    # Determine which products to run
    if args.product == "all":
        products = discover_products()
        print(f"=== Running all products: {products} ===")
    else:
        products = [args.product]

    # Clean previous outputs (keep Figma cache unless --force-figma)
    clean_folders = [SCREENSHOTS_DIR, DIFFS_DIR, REPORTS_DIR]
    if args.force_figma:
        clean_folders.append(FIGMA_FRAMES_DIR)
    for folder in clean_folders:
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)
            print(f"  Cleaned: {folder.name}/")

    # ── Phase 1: Figma (all products) ──
    print("\n" + "="*60)
    print("  PHASE 1: FIGMA")
    print("="*60)
    product_contexts = []
    for product in products:
        ctx = phase_figma(product, args)
        if ctx:
            product_contexts.append(ctx)

    if not product_contexts:
        print("No Figma frames found for any product. Exiting.")
        sys.exit(1)

    # ── Phase 2: Capture (all products) ──
    print("\n" + "="*60)
    print("  PHASE 2: BUILD & CAPTURE")
    print("="*60)
    global_built_extensions = {}
    for ctx in product_contexts:
        phase_capture(ctx, args, global_built_extensions)

    # ── Phase 3: Compare (all products) ──
    print("\n" + "="*60)
    print("  PHASE 3: COMPARE WITH CLAUDE")
    print("="*60)
    all_results = []
    for ctx in product_contexts:
        product = ctx["product"]
        print(f"\n  [{product}] Comparing...")
        results = phase_compare(ctx, args)
        all_results.extend(results)
        print(f"  [{product}] {len(results)} comparisons done")

    # ── Phase 4: Report ──
    print("\n" + "="*60)
    print("  PHASE 4: REPORT")
    print("="*60)
    if all_results:
        generate_report(all_results)
    else:
        print("No comparisons made — check Figma frame names.")

