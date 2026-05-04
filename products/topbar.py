# ── Top Bar – product-specific configuration ─────────────

# ── Platforms (identifier → ECS devEnvDeployer config) ────
# Each platform maps to a FRONTEND_IDENTIFIER for the ECS build
PLATFORMS = {
    "ecko": {
        "identifier": "eByaziBhc61JYiXhloIfk4EAyFdgT6Na9Dh4sBe3",
    },
    "casper": {
        "identifier": "oKFgZDsC2g3xZTs9cy5m62EekRbfX8h41eawryO0",
    },
    "argent": {
        "identifier": "zGqqd3RCWq9Nyssp2qHXo6LCjeCzwdgxasZO5Vzk",
    },
    "fuel": {
        "identifier": "9FaJTDcBCb48wkWKveRBg61jxfhvtg0V2iazjnA5",
    },
    "yoroi":{
        "identifier": "G1TjqDrfxS8jhRYz15Sg2fMGsvZpdFF8IWgKe0g8",
    },
    "gero":{
        "identifier": "94cnbcoEYv5A6z1yxSizi8RAa7kq71nq6miZeSNh",
    }
}

# ── Default platform for responsive tests ─────────────────
DEFAULT_PLATFORM = "ecko"

# ── Browser window height (width comes from Figma frame names) ──
DEFAULT_BROWSER_HEIGHT = 900

# ── Selectors ─────────────────────────────────────────────
# "container" = page-level wrapper (wait for it)
# "iframe"    = the extension's iframe (for hover/click inside)
# All other selectors are INSIDE the iframe
SEL = {
    "container":       "#bringweb3-offerbar-container",
    "iframe":          "#bringweb3-offerbar-container iframe",
    "activate_btn":    "#tb-activate-btn",
    "stop_offers_btn": "#tb-opt-out-btn",
    "option_24h":      "#tb-optout-24-hours-btn",
    "option_30d":      "#tb-optout-30-days-btn",
    "option_forever":  "#tb-optout-forever-btn",
    "close_btn":       "#tb-close-btn",
}

# Selectors to wait for AFTER container is visible (proves UI is loaded)
READY_SELECTORS = [SEL["activate_btn"], SEL["stop_offers_btn"]]

# ── Figma dedup config ────────────────────────────────────
# Font families used for documentation/annotation labels in Figma (not part of the UI)
FIGMA_DOC_FONTS = ["Rubik"]
# Path keywords that indicate active/current design version (higher priority in dedup)
FIGMA_PRIORITY_KEYWORDS = ["OB ", "OB_"]

# ── State that triggers opt-out (needs browser restart after) ──
TERMINAL_STATE = "success"

STATES = {
    # ── Main states ──
    "cashback offer":           [],
    "optout":                   [("click", SEL["stop_offers_btn"]), ("sleep", 1000)],
    "success":                  [("click", SEL["stop_offers_btn"]), ("sleep", 500),
                                 ("click", SEL["option_24h"]), ("sleep", 1500)],

    # ── MAIN CTA BUTTON (activate) ──
    "activate hover":           [("hover", SEL["activate_btn"]), ("sleep", 500)],

    # ── STOP OFFERS button ──
    "stop offers hover":        [("hover", SEL["stop_offers_btn"]), ("sleep", 500)],

    # ── STOP OFFERS SELECTION (option buttons) ──
    "optout 30d hover":         [("click", SEL["stop_offers_btn"]), ("sleep", 500),
                                 ("hover", SEL["option_30d"]), ("sleep", 500)],

    # ── CLOSE BUTTON - X ──
    "close hover":              [("hover", SEL["close_btn"]), ("sleep", 500)],
}

# ── Search query ──────────────────────────────────────────
SEARCH_QUERY = "lego"