import json
import re
import time
import requests
from config import FIGMA_FILES, FIGMA_FRAMES_DIR, FIGMA_TOKEN


# ── API request with retries ─────────────────────────────
def figma_request(url: str, headers: dict, retries: int = 5) -> requests.Response:
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=headers, timeout=60)
            resp.raise_for_status()
            return resp
        except (requests.RequestException, requests.HTTPError) as e:
            if attempt < retries - 1:
                wait = 2 ** (attempt + 1)
                print(f"  Figma API error ({e}), retrying in {wait}s...")
                time.sleep(wait)
            else:
                raise

# ── Color helpers ─────────────────────────────────────────
def figma_color_to_hex(color_dict):
    """Convert Figma RGBA float dict {r, g, b, a} to HEX string."""
    r = int(color_dict.get("r", 0) * 255)
    g = int(color_dict.get("g", 0) * 255)
    b = int(color_dict.get("b", 0) * 255)
    return f"#{r:02X}{g:02X}{b:02X}"

# ── Extract ALL elements dynamically ─────────────────────
def extract_all_figma_elements(node):
    elements = []
    _walk_all_figma(node, elements, depth=0, path="")
    return elements

def _walk_all_figma(node, elements, depth, path):
    name = node.get("name", "")
    ntype = node.get("type", "")

    current_path = f"{path}/{name}" if path else name

    # Structural metadata to skip — everything else is visual data for Gemini.
    _SKIP_KEYS = {
        "children", "id", "type", "name",
        "absoluteBoundingBox", "absoluteRenderBounds", "relativeTransform",
        "constraints", "exportSettings", "preserveRatio",
        "layoutAlign", "layoutGrow",
        "isMask", "locked",
        "styleOverrideTable", "characterStyleOverrides",
        "componentId", "componentProperties",
    }

    has_visual = (node.get("fills") or node.get("style") or node.get("cornerRadius")
                  or node.get("strokes") or node.get("effects") or node.get("opacity") is not None)

    if has_visual and ntype in ("FRAME", "COMPONENT", "INSTANCE", "RECTANGLE",
                                 "TEXT", "VECTOR", "ELLIPSE", "GROUP"):
        el = {
            "name": name,
            "type": ntype,
            "path": current_path,
        }

        for key, val in node.items():
            if key in _SKIP_KEYS:
                continue
            if val is None or val == [] or val == {}:
                continue
            if key == "fills" and isinstance(val, list):
                colors = [figma_color_to_hex(f["color"]) for f in val
                          if f.get("type") == "SOLID" and "color" in f and f.get("visible", True)]
                if colors:
                    el["fills"] = colors
            elif key == "strokes" and isinstance(val, list):
                stroke_colors = [figma_color_to_hex(s["color"]) for s in val
                                if "color" in s and s.get("visible", True)]
                if stroke_colors:
                    el["strokes"] = stroke_colors
            elif key == "effects" and isinstance(val, list):
                visible_effects = [e for e in val if e.get("visible", True)]
                if visible_effects:
                    for eff in visible_effects:
                        if "color" in eff:
                            eff["color_hex"] = figma_color_to_hex(eff["color"])
                    el["effects"] = visible_effects
            elif key == "absoluteBoundingBox":
                pass
            else:
                el[key] = val

        # Typography overrides (TEXT nodes — weighted vote for dominant style)
        style = node.get("style", {})
        if ntype == "TEXT" and style:
            override_table = node.get("styleOverrideTable", {})
            char_overrides = node.get("characterStyleOverrides", [])
            if override_table and char_overrides:
                override_char_counts = {}
                for oid in char_overrides:
                    override_char_counts[oid] = override_char_counts.get(oid, 0) + 1

                _FIGMA_TO_FONT_KEY = {
                    "fontFamily": "fontFamily",
                    "fontSize":   "fontSize",
                    "fontWeight": "fontWeight",
                }
                prop_votes: dict[str, dict] = {k: {} for k in _FIGMA_TO_FONT_KEY}
                for oid_str, override in override_table.items():
                    oid_int = int(oid_str) if oid_str.isdigit() else oid_str
                    chars = (override_char_counts.get(oid_int, 0)
                             or override_char_counts.get(oid_str, 1))
                    for figma_key in _FIGMA_TO_FONT_KEY:
                        val = override.get(figma_key)
                        if val is not None:
                            prop_votes[figma_key][val] = prop_votes[figma_key].get(val, 0) + chars

                for figma_key, out_key in _FIGMA_TO_FONT_KEY.items():
                    if prop_votes[figma_key]:
                        winner = max(prop_votes[figma_key], key=prop_votes[figma_key].get)
                        if "style" in el and isinstance(el["style"], dict):
                            el["style"][out_key] = winner

        # Size from bounding box
        bbox = node.get("absoluteBoundingBox", {})
        if bbox:
            w = bbox.get("width")
            h = bbox.get("height")
            if w and h:
                el["size"] = [round(w, 1), round(h, 1)]

        elements.append(el)

    for child in node.get("children", []):
        _walk_all_figma(child, elements, depth + 1, current_path)

# ── Deduplicate ──────────────────────────────────────────
def deduplicate_figma_elements(elements, prod_cfg=None):
    doc_fonts = getattr(prod_cfg, 'FIGMA_DOC_FONTS', []) if prod_cfg else []
    priority_keywords = getattr(prod_cfg, 'FIGMA_PRIORITY_KEYWORDS', []) if prod_cfg else []

    # Step 1: Drop auto-generated names
    generic_pattern = re.compile(r'^(Rectangle|Frame|Vector|Ellipse|Group)\s*\d*$', re.IGNORECASE)
    meaningful = [el for el in elements if not generic_pattern.match(el["name"])]

    # Step 2: Drop white-only decorative spacers
    def is_decorative_white(el):
        fills = el.get("fills", [])
        if not fills or not all(c.upper() in ("#FFFFFF", "#FFF") for c in fills):
            return False
        return "style" not in el and "strokes" not in el and "cornerRadius" not in el

    meaningful = [el for el in meaningful if not is_decorative_white(el)]

    # Step 3: Drop documentation labels
    hex_color_pattern = re.compile(r'^[0-9A-Fa-f]{6}$')

    def is_doc_label(el):
        style = el.get("style", {})
        if doc_fonts and style.get("fontFamily") in doc_fonts and style.get("fontSize", 0) >= 15:
            return True
        if hex_color_pattern.match(el.get("name", "").strip()):
            return True
        return False

    meaningful = [el for el in meaningful if not is_doc_label(el)]

    # Step 4: Assign priority by path
    def path_priority(el):
        path = el.get("path", "")
        if any(kw in path for kw in priority_keywords):
            return 2
        if len(re.findall(r'/Group\s', path)) == 0:
            return 1
        return 0

    # Step 5: Z-order tiebreaker
    meaningful.reverse()
    meaningful.sort(key=path_priority, reverse=True)

    # Step 6: Deduplicate
    seen_sigs = set()
    seen_name_fonts = {}
    unique = []

    for el in meaningful:
        name = el["name"]

        sig = (
            name,
            tuple(el.get("fills", [])),
            tuple(el.get("strokes", [])),
            el.get("cornerRadius"),
            json.dumps(el.get("style"), sort_keys=True) if el.get("style") else None,
        )
        if sig in seen_sigs:
            continue
        seen_sigs.add(sig)

        style = el.get("style", {})
        if style:
            font_size = style.get("fontSize")
            if name in seen_name_fonts:
                if font_size in seen_name_fonts[name]:
                    continue
                el["name"] = f"{name} ({font_size}px)"
            seen_name_fonts.setdefault(name, set()).add(font_size)
        else:
            if name in seen_name_fonts:
                continue
            seen_name_fonts.setdefault(name, set()).add(None)

        unique.append(el)

    return unique

# ── Download Figma frames ────────────────────────────────
def download_figma_frames(product: str):
    file_key = FIGMA_FILES.get(product)
    if not file_key:
        print(f"ERROR: No Figma file key for product '{product}'")
        return []

    headers = {"X-Figma-Token": FIGMA_TOKEN}
    url = f"https://api.figma.com/v1/files/{file_key}"
    resp = figma_request(url, headers)
    data = resp.json()

    frames = {}
    frame_nodes = {}

    def walk(node):
        name = node.get("name", "")
        if name.startswith(f"{product}_"):
            frames[node["id"]] = name
            frame_nodes[name] = node
        for child in node.get("children", []):
            walk(child)

    walk(data["document"])
    print(f"Found {len(frames)} frames for '{product}': {list(frames.values())}")

    if not frames:
        return [], {}

    ids_param = ",".join(frames.keys())
    img_url = f"https://api.figma.com/v1/images/{file_key}?ids={ids_param}&format=png&scale=1"
    img_resp = figma_request(img_url, headers)
    images = img_resp.json().get("images", {})

    FIGMA_FRAMES_DIR.mkdir(exist_ok=True)
    saved = []
    for node_id, image_url in images.items():
        frame_name = frames[node_id]
        file_path = FIGMA_FRAMES_DIR / f"{frame_name}.png"
        img_data = requests.get(image_url).content
        file_path.write_bytes(img_data)
        print(f"  Saved: {file_path.name}")
        saved.append(file_path)

    return saved, frame_nodes

# ── Parse Figma frame names ──────────────────────────────
def parse_figma_frames(product: str, frame_names: list[str]):
    responsive = []
    styles = []

    for name in frame_names:
        parts = name.split("_")
        if len(parts) < 2:
            continue
        second = parts[1]
        if second.isdigit():
            viewport = second
            state = "_".join(parts[2:]) if len(parts) > 2 else "default"
            responsive.append((name, int(viewport), state))
        else:
            platform = "_".join(parts[1:]).lower()
            styles.append((name, platform))

    return responsive, styles
