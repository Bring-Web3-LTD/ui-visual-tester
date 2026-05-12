import json
import re
import time
import base64
import anthropic
from PIL import Image
from pixelmatch import pixelmatch
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL, DIFFS_DIR


# ── Pixel-level diff ─────────────────────────────────────
def pixel_diff(screenshot_path, figma_path) -> dict:
    DIFFS_DIR.mkdir(exist_ok=True)

    img_a = Image.open(screenshot_path).convert("RGBA")
    img_b = Image.open(figma_path).convert("RGBA")

    w, h = img_a.width, img_a.height
    img_b = img_b.resize((w, h), Image.LANCZOS)

    output = bytearray(w * h * 4)
    mismatch = pixelmatch(
        img_a.tobytes(), img_b.tobytes(), w, h,
        output,
        threshold=0.15,
        alpha=0.5,
    )

    total_px = w * h
    pct = round((mismatch / total_px) * 100, 2) if total_px else 0

    diff_path = DIFFS_DIR / f"diff_{screenshot_path.stem}.png"
    diff_img = Image.frombytes("RGBA", (w, h), bytes(output))
    diff_img.save(str(diff_path))

    print(f"  Pixel diff: {mismatch:,}/{total_px:,} pixels differ ({pct}%)")
    return {
        "mismatch": mismatch,
        "total": total_px,
        "pct": pct,
        "diff_path": diff_path,
        "size": (w, h),
    }

# ── GitHub Models: single-state style comparison (internal) ─────
def _img_to_base64(path) -> str:
    return base64.standard_b64encode(path.read_bytes()).decode("utf-8")

def _img_to_content_block(path) -> dict:
    """Build an Anthropic image content block from a file path."""
    b64 = _img_to_base64(path)
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": b64},
    }

def _compare_single_state(client, figma_elements, state_name, dom_styles,
                          screenshot_path, spec_path, product_name="topbar") -> dict:
    messages_content = []

    figma_text = json.dumps(figma_elements, indent=2, ensure_ascii=False)
    dom_text = json.dumps({state_name: dom_styles}, indent=2, ensure_ascii=False)

    if spec_path and spec_path.exists():
        messages_content.append({"type": "text", "text": "FIGMA SPEC SHEET (shows all UI states/variants):"})
        messages_content.append(_img_to_content_block(spec_path))

    messages_content.append({"type": "text", "text": f"Live screenshot — {screenshot_path.stem}:"})
    messages_content.append(_img_to_content_block(screenshot_path))

    prompt = f"""Design QA: compare the "{state_name}" state of a browser extension {product_name}.
You receive a Figma spec image (all states/variants), a live screenshot, Figma JSON data, and DOM CSS data.
Match each Figma element to its DOM counterpart by name/keyword (e.g. "cashback" in Figma → element containing "cashback" in DOM).

FIGMA DATA:
```json
{figma_text}
```

DOM DATA:
```json
{dom_text}
```

Automatically map each Figma property to its equivalent DOM CSS property (e.g. fills→background-color, cornerRadius→border-radius, etc.). Compare only properties present in BOTH datasets.

COMPARISON RULES:
1. TRUTH PRIORITY: Spec image > Live screenshot > DOM CSS values > Figma JSON values.
   When sources conflict, trust the higher-priority source.
2. Only compare properties that exist in BOTH Figma and DOM data. Never invent or guess values.
   If DOM data is empty/missing for a property, SKIP that check — do not FAIL it.
3. COLOR TOLERANCE: colors within ΔE ≤ 5 (roughly ±10 RGB per channel) → PASS.
   Hover/active states may have slight opacity changes → PASS.
4. SIZE TOLERANCE: width/height within ±10% or ±5px (whichever is larger) → PASS.
   Width diff > 50% → FAIL. Between 10%-50% → FAIL only if clearly visible in screenshot.
5. FONT: same visual family → PASS (e.g. "Inter" vs "Inter, sans-serif" → PASS).
   Same size ±1px → PASS.
6. CROSS-STATE RULE: An element visible in ANY provided screenshot is NOT missing.
   Only FAIL "missing element" if it appears in Figma but is absent from ALL screenshots.
7. HOVER STATES: Slight color lightening/darkening on hover elements → PASS.
   Completely different hue on hover → FAIL.
8. IGNORE: dynamic text content differences, Figma annotations/comments, layer ordering.

OUTPUT — valid JSON only, no markdown wrapping:
{{"checks": [{{"element": "name", "property": "prop", "figma": "val", "dom": "val", "status": "PASS|FAIL", "severity": "high|medium|low", "note": "brief reason", "state": "{state_name}"}}], "visual_issues": [{{"description": "text", "severity": "high|medium|low"}}], "summary": {{"total": N, "passed": N, "failed": N, "high": N, "medium": N, "low": N, "verdict": "PASS|FAIL"}}}}

SEVERITY GUIDE:
- high: missing element, wrong color hue, layout broken, size off >50%
- medium: spacing off 5–15px, font weight mismatch, border-radius wrong
- low: size off 10–50%, color shade slightly off, minor font-size diff"""

    messages_content.append({"type": "text", "text": prompt})

    for attempt in range(5):
        try:
            response = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=4096,
                messages=[{"role": "user", "content": messages_content}],
            )
            break
        except anthropic.RateLimitError:
            wait = 30 * (attempt + 1)
            print(f"    Rate limited, waiting {wait}s...")
            time.sleep(wait)
        except anthropic.APIStatusError as e:
            if e.status_code in (429, 503, 529):
                wait = 30 * (attempt + 1)
                print(f"    API issue ({str(e)[:80]}...), waiting {wait}s...")
                time.sleep(wait)
            else:
                raise
    else:
        raise RuntimeError(f"Anthropic API failed after 5 retries (state: {state_name})")

    raw_text = response.content[0].text.strip()
    try:
        cleaned = raw_text
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        return json.loads(cleaned)
    except json.JSONDecodeError:
        json_match = re.search(r'\{[\s\S]*\}', raw_text)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass
        return {"checks": [], "visual_issues": [raw_text],
                "summary": {"total": 0, "passed": 0, "failed": 0, "verdict": "FAIL"}}


# ── GitHub Models: style comparison (splits per state) ───
RATE_LIMIT_DELAY = 4  # seconds between API calls

def claude_compare_style(figma_elements, dom_elements, screenshots,
                         spec_path=None, state_name="unknown", product_name="topbar") -> dict:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    # Build state→screenshot mapping
    state_shots = []
    for shot_path in screenshots:
        # Extract state from filename: style_topbar_Ecko_cashback_offer.png → cashback offer
        stem = shot_path.stem
        for state in dom_elements:
            safe = state.replace(" ", "_")
            if stem.endswith(safe):
                state_shots.append((state, shot_path, dom_elements[state]))
                break
        else:
            # Fallback: derive state from filename
            parts_list = stem.split("_")
            state_guess = " ".join(parts_list[3:]) if len(parts_list) > 3 else stem
            state_shots.append((state_guess, shot_path, dom_elements.get(state_guess, {})))

    all_checks = []
    all_issues = []
    total_pass = 0
    total_fail = 0

    for i, (st_name, shot_path, dom_styles) in enumerate(state_shots):
        print(f"\n    Comparing state '{st_name}' ({i+1}/{len(state_shots)})...")

        result = _compare_single_state(
            client, figma_elements, st_name, dom_styles, shot_path, spec_path,
            product_name=product_name
        )

        checks = result.get("checks", [])
        issues = result.get("visual_issues", [])

        passed = sum(1 for c in checks if c.get("status") == "PASS")
        failed = sum(1 for c in checks if c.get("status") == "FAIL")

        print(f"      {st_name}: {len(checks)} checks, {passed} pass, {failed} fail")
        for c in checks:
            if c.get("status") == "FAIL":
                sev = c.get('severity', '?')
                print(f"        x [{sev}] {c.get('element')} > {c.get('property')}: "
                      f"expected {c.get('figma')}, got {c.get('dom')}")

        all_checks.extend(checks)
        all_issues.extend(issues)
        total_pass += passed
        total_fail += failed

        # Rate limit delay between calls (skip after last)
        if i < len(state_shots) - 1:
            print(f"    Waiting {RATE_LIMIT_DELAY}s (rate limit)...")
            time.sleep(RATE_LIMIT_DELAY)

    # Merged result
    verdict = "PASS" if total_fail == 0 else "FAIL"
    total = total_pass + total_fail

    sev_high = sum(1 for c in all_checks if c.get("status") == "FAIL" and c.get("severity") == "high")
    sev_med = sum(1 for c in all_checks if c.get("status") == "FAIL" and c.get("severity") == "medium")
    sev_low = sum(1 for c in all_checks if c.get("status") == "FAIL" and c.get("severity") == "low")

    merged = {
        "checks": all_checks,
        "visual_issues": all_issues,
        "summary": {
            "total": total, "passed": total_pass, "failed": total_fail,
            "high": sev_high, "medium": sev_med, "low": sev_low,
            "verdict": verdict,
        },
    }

    print(f"\n=== Style summary ({len(state_shots)} states) ===")
    print(f"  Total: {total} | Pass: {total_pass} | Fail: {total_fail} "
          f"(high: {sev_high}, medium: {sev_med}, low: {sev_low}) | {verdict}")

    return merged

# ── GitHub Models: responsive visual comparison ──────────
def claude_compare(screenshot_path, figma_path, diff_stats=None, product_name="topbar") -> str:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    content = [
        _img_to_content_block(screenshot_path),
        _img_to_content_block(figma_path),
    ]

    diff_info = ""
    if diff_stats:
        diff_info = f"""\n\nPixel diff results: {diff_stats['mismatch']:,} pixels differ out of {diff_stats['total']:,} ({diff_stats['pct']}%).
    Images were compared at {diff_stats['size'][0]}x{diff_stats['size'][1]}."""

    prompt = f"""Visual QA: Compare a browser extension {product_name} across two images.
Image 1 = live browser screenshot. Image 2 = Figma design reference.
{diff_info}

COMPARISON CATEGORIES (check each):
1. LAYOUT & POSITIONS — Are elements in the same relative positions? Same left-to-right order?
2. SIZES — Are containers, buttons, icons roughly the same size? (tolerance: ±10% or ±5px)
3. SPACING — Are gaps between elements consistent? Padding inside containers similar?
4. ALIGNMENT — Are elements vertically/horizontally centered the same way?
5. COLORS — Are background colors, text colors, border colors matching? (tolerance: ΔE ≤ 5)
6. TYPOGRAPHY — Similar font sizes and weights? (tolerance: ±1px size, visual weight match)
7. STRUCTURE — Same number of visible sections/groups? Any missing or extra components?
8. CONTAINER HEIGHT — Is the overall {product_name} height similar?

IGNORE (always PASS these):
- Different text content, logos, or icons (these are dynamic/per-retailer)
- RTL vs LTR text direction
- Anti-aliasing differences and sub-pixel rendering
- Pixel-diff red highlighting on text areas (text is dynamic)
- Minor opacity differences on hover states

FAIL ONLY WHEN:
- Clearly different hue/color (not just brightness)
- Element size off by more than 50%
- Element completely missing or extra element added
- Layout is structurally broken (overlapping, overflow, wrong axis)
- Spacing off by more than 15px

For each category, also assign a SEVERITY (high, medium, low):
- high: missing element, completely wrong color, layout broken, size off >50%
- medium: spacing off 5–15px, font weight mismatch, border-radius wrong
- low: minor size diff 10–50%, slight color shade, minor font-size diff

FORMAT each category as:
"N. Category: PASS/FAIL (severity) — [brief observation]"

End with exactly: OVERALL: PASS or OVERALL: FAIL"""

    if diff_stats and diff_stats.get("diff_path"):
        content.append(_img_to_content_block(diff_stats["diff_path"]))
        content.append({"type": "text", "text": "Image 3 above is the pixel-diff heatmap (red = different pixels).\n\n" + prompt})
    else:
        content.append({"type": "text", "text": prompt})

    for attempt in range(5):
        try:
            response = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=2000,
                messages=[{"role": "user", "content": content}],
            )
            break
        except anthropic.RateLimitError:
            wait = 30 * (attempt + 1)
            print(f"  Rate limited, waiting {wait}s...")
            time.sleep(wait)
        except anthropic.APIStatusError as e:
            if e.status_code in (429, 503, 529):
                wait = 30 * (attempt + 1)
                print(f"  API issue ({str(e)[:80]}...), waiting {wait}s...")
                time.sleep(wait)
            else:
                raise
    else:
        raise RuntimeError("Anthropic API failed after 5 retries")

    result = response.content[0].text
    print(f"\n=== AI Compare: {screenshot_path.name} vs {figma_path.name} ===")
    print(result)
    return result
