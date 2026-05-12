from products import PLATFORMS 

# ── Default platform for responsive tests ─────────────────
DEFAULT_PLATFORM = "ecko"

# ── Browser window height (width comes from Figma frame names) ──
DEFAULT_BROWSER_HEIGHT = 900

# ── Selectors ─────────────────────────────────────────────
# "container" = the iframe itself (page-level, no outer wrapper)
# "iframe"    = same — popup is injected as a standalone iframe
# All other selectors are INSIDE the iframe
SEL = {
    "container":       "iframe[id^='bringweb3-iframe-']",
    "iframe":          "iframe[id^='bringweb3-iframe-']",
    # ── Offer screen ──
    "activate_btn":    "#activate-btn",
    "opt_out_btn":     "#opt-out-btn",
    "cancel_btn":      "#cancel-btn",
    "close_btn":       "#close-btn",
    # ── Opt-out screen ──
    "opt_out_apply":   "#opt-out-apply-btn",
    "opt_out_back":    "#opt-out-back-btn",
    "radio_website_0": "#websiteOption0",   # For this website
    "radio_website_1": "#websiteOption1",   # For all websites
    "radio_dur_0":     "#durationOption0",  # 24 hours
    "radio_dur_1":     "#durationOption1",  # 30 days
    "radio_dur_2":     "#durationOption2",  # forever
}

# Selectors to wait for AFTER container is visible (proves UI is loaded)
READY_SELECTORS = [SEL["activate_btn"], SEL["opt_out_btn"]]

# ── Figma dedup config ────────────────────────────────────
FIGMA_DOC_FONTS = []
FIGMA_PRIORITY_KEYWORDS = []

# ── State that triggers a terminal action (needs browser restart after) ──
TERMINAL_STATE = "success"

# ── States ────────────────────────────────────────────────
# Each state = list of (action_type, selector_or_value) tuples
# Actions: "click", "hover", "sleep" (ms)
STATES = {
    # ── Main states ──
    "cashback offer":  [],
    "optout":          [("click", SEL["opt_out_btn"]), ("sleep", 1000)],
    "success":         [("click", SEL["opt_out_btn"]), ("sleep", 500),
                        ("click", SEL["opt_out_apply"]), ("sleep", 1500)],

    # ── Offer screen hovers ──
    "activate hover":  [("hover", SEL["activate_btn"]), ("sleep", 500)],
    "opt out hover":   [("hover", SEL["opt_out_btn"]), ("sleep", 500)],
    "close hover":     [("hover", SEL["close_btn"]), ("sleep", 500)],

    # ── Opt-out screen ──
    "optout 30d":      [("click", SEL["opt_out_btn"]), ("sleep", 500),
                        ("click", SEL["radio_dur_1"]), ("sleep", 500)],
    "optout forever":  [("click", SEL["opt_out_btn"]), ("sleep", 500),
                        ("click", SEL["radio_dur_2"]), ("sleep", 500)],
    "optout apply hover": [("click", SEL["opt_out_btn"]), ("sleep", 500),
                           ("hover", SEL["opt_out_apply"]), ("sleep", 500)],
}

# ── Search query ──────────────────────────────────────────
SEARCH_QUERY = "lego.com"
