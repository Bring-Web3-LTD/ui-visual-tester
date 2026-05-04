import json
import re
import time
from google import genai
from google.genai import types
from PIL import Image
from pixelmatch import pixelmatch
from config import GEMINI_API_KEY, GEMINI_MODEL, DIFFS_DIR


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

# ── Gemini: intelligent style comparison ─────────────────
def gemini_compare_style(figma_elements, dom_elements, screenshots,spec_path=None, state_name="unknown") -> dict:
    client = genai.Client(api_key=GEMINI_API_KEY)
    parts = []

    figma_text = json.dumps(figma_elements, indent=2, ensure_ascii=False)
    dom_text = json.dumps(dom_elements, indent=2, ensure_ascii=False)

    if spec_path and spec_path.exists():
        parts.append("FIGMA SPEC SHEET (shows all UI states/variants):")
        parts.append(types.Part.from_bytes(data=spec_path.read_bytes(), mime_type="image/png"))

    for shot_path in screenshots:
        parts.append(f"Live screenshot — {shot_path.stem}:")
        parts.append(types.Part.from_bytes(data=shot_path.read_bytes(), mime_type="image/png"))

    prompt = f"""You are a design QA expert. Compare live browser DOM data + screenshots against a Figma design spec.

## FIGMA DESIGN DATA (from Figma REST API — all visual properties from the active design):
```json
{figma_text}
```

## LIVE DOM DATA (from getComputedStyle — all non-trivial computed styles, organized by state):
Each DOM element has an `id`, `tag`, `text`, `width`, `height`, and a `styles` dict containing
every CSS property that has a non-empty value. Property names are in CSS kebab-case (e.g. "font-size").
```json
{dom_text}
```

## INSTRUCTIONS:
Match each DOM/screenshot state to its Figma counterpart by keyword (e.g. "hover" → hover variant).
Compare ALL visual properties: colors, typography, border-radius, padding, opacity, borders, shadows.
Map Figma properties to CSS: fills→background-color, style.fontFamily→font-family, cornerRadius→border-radius, strokeWeight→border-width, effects→box-shadow.

## TRUTH HIERARCHY (highest to lowest):
1. Figma SPEC IMAGE — visual source of truth for design intent
2. Live SCREENSHOT — what the user actually sees
3. DOM CSS values — precise but may miss visual context
4. Figma JSON — may contain stale/mixed data from dedup; IGNORE if it contradicts the spec image

## PASS rules (mark PASS, not FAIL):
- Visually similar colors (e.g. #1A1A1A vs #000000 — both look black)
- Slight hover opacity changes (standard interactive feedback)
- Dynamic text content differences (names, prices, widths)
- Font-family mismatch in JSON if fonts look identical in images
- Anti-aliasing / sub-pixel rendering differences

## FAIL only when:
- Color difference is CLEARLY VISIBLE (different hue or obviously lighter/darker)
- Fundamentally wrong style (wrong element, wrong layout, missing component)
- Width difference >50% suggesting layout break

## CROSS-STATE rule:
- You are given MULTIPLE state screenshots. If an element (e.g. close button X) is visible in ANY screenshot, it is NOT missing — mark PASS.
- Only mark "presence: FAIL" if the element is absent from ALL screenshots.

## IGNORE: dynamic text content, Figma doc labels/annotations

## OUTPUT — valid JSON, no markdown:
{{"checks": [{{"element": "name", "property": "prop", "figma": "value", "dom": "value", "status": "PASS", "state": "state name"}}], "visual_issues": ["description"], "summary": {{"total": N, "passed": N, "failed": N, "verdict": "PASS|FAIL"}}}}
"verdict": "PASS" if 0 failures, else "FAIL". Show exact values from both sides."""

    parts.append(prompt)

    for attempt in range(5):
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=parts,
                config=types.GenerateContentConfig(
                    max_output_tokens=65536,
                    response_mime_type="application/json",
                ),
            )
            break
        except Exception as e:
            err = str(e).lower()
            if "429" in str(e) or "rate" in err or "503" in str(e) or "unavailable" in err:
                wait = 60 * (attempt + 1)
                print(f"  API issue ({str(e)[:80]}...), waiting {wait}s...")
                time.sleep(wait)
            else:
                raise
    else:
        raise RuntimeError("Gemini API rate limit exceeded after 5 retries")

    # Check for truncation
    try:
        finish = response.candidates[0].finish_reason
        if finish and str(finish) not in ("STOP", "FinishReason.STOP", "1"):
            print(f"  WARNING: Gemini response may be truncated (finish_reason={finish})")
    except (IndexError, AttributeError):
        pass

    raw_text = response.text.strip()

    try:
        cleaned = raw_text
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        result = json.loads(cleaned)
    except json.JSONDecodeError:
        json_match = re.search(r'\{[\s\S]*\}', raw_text)
        if json_match:
            try:
                result = json.loads(json_match.group())
            except json.JSONDecodeError:
                result = {"checks": [], "visual_issues": [raw_text],
                          "summary": {"total": 0, "passed": 0, "failed": 0, "verdict": "FAIL"},
                          "raw_response": raw_text}
        else:
            result = {"checks": [], "visual_issues": [raw_text],
                      "summary": {"total": 0, "passed": 0, "failed": 0, "verdict": "FAIL"},
                      "raw_response": raw_text}

    summary = result.get("summary", {})
    checks = result.get("checks", [])
    failed_checks = [c for c in checks if c.get("status") == "FAIL"]

    print(f"\n=== Style: {[s.stem for s in screenshots][:2]}... ({len(screenshots)} states) ===")
    print(f"  Total: {summary.get('total', len(checks))} | "
          f"Pass: {summary.get('passed', len(checks) - len(failed_checks))} | "
          f"Fail: {summary.get('failed', len(failed_checks))} | "
          f"{summary.get('verdict', 'UNKNOWN')}")
    if failed_checks:
        for c in failed_checks[:8]:
            print(f"    x {c.get('element')} > {c.get('property')}: "
                  f"expected {c.get('figma')}, got {c.get('dom')}")
        if len(failed_checks) > 8:
            print(f"    ... and {len(failed_checks) - 8} more failures")

    return result

# ── Gemini: responsive visual comparison ─────────────────
def gemini_compare(screenshot_path, figma_path, diff_stats=None) -> str:
    client = genai.Client(api_key=GEMINI_API_KEY)

    parts = [
        types.Part.from_bytes(data=screenshot_path.read_bytes(), mime_type="image/png"),
        types.Part.from_bytes(data=figma_path.read_bytes(), mime_type="image/png"),
    ]

    diff_info = ""
    if diff_stats:
        diff_info = f"""\n\nPixel diff results: {diff_stats['mismatch']:,} pixels differ out of {diff_stats['total']:,} ({diff_stats['pct']}%).
    Images were compared at {diff_stats['size'][0]}x{diff_stats['size'][1]}."""

    prompt = f"""You are a visual QA reviewer.

Compare these two images:
- Image 1: Screenshot of a live UI element from the browser
- Image 2: Figma design of the same UI element
{diff_info}

IMPORTANT — IGNORE these differences (they are NOT bugs):
- Different text content (dynamic data like names, prices, percentages)
- Different logos or brand icons — these change per context
- RTL vs LTR text direction — this is locale, not a bug
- Minor anti-aliasing or font rendering differences
- Pixel diff heatmap showing red on text/logo areas — dynamic content

ONLY flag as FAIL if there is a STRUCTURAL or DESIGN difference:
- Wrong background color, button color, or border color
- Wrong element sizes (buttons/icons disproportionate to design)
- Wrong spacing/padding between structural elements
- Missing or extra UI elements
- Layout structure broken (sections in wrong order or size)

Check:
1. Element positions — correct layout structure?
2. Element sizes — proportional to design?
3. Spacing — correct margins/padding?
4. Alignment — vertically/horizontally correct?
5. Overall structure — layout matches design?
6. Font sizes — approximately match?
7. Container height — approximately matches?

Format:
1. Positions: PASS/FAIL — [specific observation]
2. Sizes: PASS/FAIL — [specific observation]
...

End with exactly: OVERALL: PASS or OVERALL: FAIL"""

    if diff_stats and diff_stats.get("diff_path"):
        parts.append(types.Part.from_bytes(data=diff_stats["diff_path"].read_bytes(), mime_type="image/png"))
        parts.append("Image 3 above is the pixel-diff heatmap (red = different pixels).\n\n" + prompt)
    else:
        parts.append(prompt)

    for attempt in range(5):
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=parts,
                config=types.GenerateContentConfig(max_output_tokens=2000),
            )
            break
        except Exception as e:
            err = str(e).lower()
            if "429" in str(e) or "rate" in err or "503" in str(e) or "unavailable" in err:
                wait = 60 * (attempt + 1)
                print(f"  API issue ({str(e)[:80]}...), waiting {wait}s...")
                time.sleep(wait)
            else:
                raise
    else:
        raise RuntimeError("Gemini API rate limit exceeded after 5 retries")

    result = response.text
    print(f"\n=== Gemini: {screenshot_path.name} vs {figma_path.name} ===")
    print(result)
    return result
