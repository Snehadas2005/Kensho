"""
Kensho v2 - UI theme
====================
Customise the UI from the blocks at the top of this file:

    SIDEBAR_SCALE / MAIN_SCALE  - one knob each to make ALL text smaller / bigger
    SIDEBAR                     - sidebar width, padding, font sizes, heights, radius
    MAIN                        - masthead, banner, metrics, tabs, background image
    PALETTE                     - every colour for light + dark modes
    FONTS / LOCAL_FONTS         - font stacks and local font files

Default theme is LIGHT.
To prevent Streamlit from auto-switching to dark mode based on OS/browser
`prefers-color-scheme`, also add a `.streamlit/config.toml` file with:

    [theme]
    base = "light"

And call `theme_toggle()` at the top of `with st.sidebar:` to render the
Light / Dark switch shown in the UI design.
"""
from __future__ import annotations

import base64
import html
import re
from pathlib import Path

import streamlit as st

BASE_DIR = Path(__file__).resolve().parent
ASSET_DIRS = [BASE_DIR / "assets", BASE_DIR, BASE_DIR.parent / "assets", BASE_DIR.parent]
FONT_DIRS = [BASE_DIR / "assets" / "fonts", BASE_DIR / "fonts", BASE_DIR.parent / "assets" / "fonts"]

# ---------------------------------------------------------------------------
# 0. GLOBAL SCALE KNOBS  (1.0 = values below; 0.85 = 15% smaller; 1.2 = 20% bigger)
# ---------------------------------------------------------------------------
SIDEBAR_SCALE = 1.0   # multiplies every fs_* font size in SIDEBAR
MAIN_SCALE = 1.0      # multiplies every fs_* font size in MAIN

# ---------------------------------------------------------------------------
# 1. SIDEBAR  (numbers named fs_* are font sizes in px)
# ---------------------------------------------------------------------------
SIDEBAR = {
    "width": "28vw",     # sidebar width (1/3 of viewport). Try "28vw" or "420px"
    "pad_x": "1.5rem",       # left / right inner padding (25.6px at 16px root)
    "pad_top": "0.1rem",     # top inner padding (6.4px)
    "gap": "0.4rem",         # vertical gap between sidebar widgets (6.4px)
    "fs_title": 28,          # "Target" heading              (MADE Mirage, bold)
    "fs_label": 17,          # italic field labels           (Apparatus SIL, italic)
    "fs_input": 15,          # text inside inputs/selects    (DM Sans, light)
    "fs_slider": 15,         # numeric value above slider    (DM Sans, regular)
    "fs_button": 16,         # "Run Triage & Repro" button   (DM Sans, regular)
    "fs_meta": 12.5,         # info block at sidebar bottom  (Gotham, light)
    "input_h": "2.6rem",     # height of text/number/select boxes (~41.6px)
    "button_h": "2.8rem",    # height of primary Run button (~44.8px)
    "radius": "8px",         # corner radius of inputs + Run button
    "gap": "1.2rem",   # vertical gap between sidebar widgets (was "0.4rem")
}

# ---------------------------------------------------------------------------
# 2. MAIN AREA
# ---------------------------------------------------------------------------
MAIN = {
    # Background watermark image (kensho-bg-light.png / kensho-bg-dark.png)
    "bg_w": "min(70vw, 96vh)",   # image width, responsive to viewport
    "bg_cx": 0.5,               # horizontal centre inside main area (0 = left, 1 = right)
    "bg_y": "58%",               # vertical position from top
    # Masthead
    "fs_title": 72,   "ls_title": "8.6px",      # "KENSHO" wordmark (72px, wide tracking)
    "fs_badge": 12,   "ls_badge": "1.35px",     # "(v2.0.0)" pill badge
    "fs_sub": 15,     "ls_sub": "1.12px",       # subtitle below wordmark
    # Results area
    "fs_caption": 16, "ls_caption": "0.4px",    # "Showing your last saved run..."
    "fs_banner": 17,                            # status banner title (VERIFIED / ERROR)
    "fs_banner_body": 14,                       # status banner issue description
    "fs_metric_label": 15,                      # metric labels (Status / Runtime / ...)
    "fs_metric_value": 34, "ls_metric": "0.76px",  # metric values (VERIFIED / PYTHON / ...)
    "fs_tab": 17,                               # tab titles (Triage / Test / Sandbox / Report)
    "fs_body": 16,    "ls_body": "0.4px",       # body copy inside tabs
}

# ---------------------------------------------------------------------------
# 3. PALETTE
# ---------------------------------------------------------------------------
PALETTE = {
    "light": {
        "bg": "#FFFFFF",                        # main page background
        "side_bg": "#2A2A2A",                   # sidebar background (charcoal)
        "side_text": "#FFFFFF",                 # sidebar text + icons
        "input_bg": "#4A4A4A",                  # sidebar input fill (opaque)
        "input_border": "#6B6B6B",              # sidebar input border
        "text": "#000000",                      # primary main-area text
        "text2": "#676767",                     # secondary / caption text
        "muted": "#939393",                     # muted text
        "blue": "#4186EC",                      # primary blue (KENSHO title, slider)
        "blue_alt": "#3E7AD3",                  # badge text blue
        "blue_tint": "rgba(174,206,255,0.74)",  # badge pill fill
        "btn_bg": "#3E7AD3",                    # Run button fill
        "btn_text": "#FFFFFF",                  # Run button text
        "red": "#DC0000",                       # active tab underline + error accent
        "red_tint": "rgba(220,0,0,0.12)",       # error banner / pill fill
        "green": "#00A220",                     # success banner border
        "green_text": "#016802",                # success banner text
        "green_tint": "rgba(1,176,36,0.40)",    # success banner fill
        "warn": "#B45309",                      # warning border + text
        "warn_tint": "rgba(245,158,11,0.25)",   # warning banner fill
        "divider": "#D9D9D9",                   # borders, tab rule, code/expander outlines
        "code_bg": "#F3F4F6",                   # code block background
    },
    "dark": {
        "bg": "#121212",                        # main page background
        "side_bg": "#1E1E1E",                   # sidebar background
        "side_text": "#F3F4F6",                 # sidebar text + icons
        "input_bg": "#2E2E2E",                  # sidebar input fill (opaque)
        "input_border": "#4A4A4A",              # sidebar input border
        "text": "#F3F4F6",                      # primary main-area text
        "text2": "#A3A3A3",                     # secondary / caption text
        "muted": "#737373",                     # muted text
        "blue": "#5B9BF8",                      # primary blue
        "blue_alt": "#6CA6FA",                  # badge text blue
        "blue_tint": "rgba(59,130,246,0.25)",   # badge pill fill
        "btn_bg": "#5B9BF8",                    # Run button fill
        "btn_text": "#0B1220",                  # Run button text
        "red": "#F87171",                       # active tab + error accent
        "red_tint": "rgba(248,113,113,0.18)",   # error banner / pill fill
        "green": "#10B981",                     # success banner border
        "green_text": "#34D399",                # success banner text
        "green_tint": "rgba(16,185,129,0.22)",  # success banner fill
        "warn": "#FBBF24",                      # warning border + text
        "warn_tint": "rgba(251,191,36,0.18)",   # warning banner fill
        "divider": "#3A3A3A",                   # borders, tab rule, code/expander outlines
        "code_bg": "#1E1E1E",                   # code block background
    },
}

# ---------------------------------------------------------------------------
# 4. FONTS
# ---------------------------------------------------------------------------
FONTS = {
    "display": "'MADE Mirage', Georgia, serif",            # KENSHO wordmark, "Target"
    "label": "'Apparatus SIL', Georgia, serif",             # italic labels + tab titles
    "ui": "'DM Sans', Arial, sans-serif",                   # sidebar inputs + Run button
    "result": "'Google Sans', 'DM Sans', Arial, sans-serif",  # results area + metrics
    "sub": "'Palo', 'DM Sans', Arial, sans-serif",          # masthead subtitle
    "badge": "'FreeSans', Arial, sans-serif",               # version badge "(v2.0.0)"
    "meta": "'Gotham', 'DM Sans', Arial, sans-serif",       # sidebar bottom info block
}

# Files in assets/fonts/ (matched case-insensitively across .ttf .otf .woff .woff2).
# Weight "100 900" tells the browser to use the file across all weights.
LOCAL_FONTS = {
    "MADE Mirage": [("MADE Mirage", "100 900", "normal")],
    "Palo": [("Palo", "100 900", "normal")],
    "Gotham": [("Gotham", "100 900", "normal")],
    "Google Sans": [("Google Sans", "100 900", "normal")],
    "FreeSans": [("FreeSans", "100 900", "normal")],
    "Apparatus SIL": [("Apparatus SIL", "100 900", "normal")],
    "DM Sans": [("DM Sans", "100 900", "normal")],
}

# Optional remote @import URLs (empty when all fonts are loaded locally)
GOOGLE_IMPORTS: list[str] = []


# ---------------------------------------------------------------------------
# 5. HELPERS & THEME SWITCHING
# ---------------------------------------------------------------------------
def esc(value) -> str:
    return html.escape(str(value if value is not None else ""))


def current_mode() -> str:
    """
    Return 'light' (default) or 'dark'.

    Priority:
      1. Explicit user toggle stored in `st.session_state['kensho_theme']`
      2. Always defaults to 'light' so OS dark mode does not hijack the UI
    """
    mode = st.session_state.get("kensho_theme")
    if mode in ("light", "dark"):
        return mode
    return "light"


def theme_toggle(label: str = "Theme") -> str:
    """
    Render a clean Light/Dark switch (intended for the top of `with st.sidebar:`).
    Returns the active mode ('light' or 'dark').
    """
    if "kensho_theme" not in st.session_state:
        st.session_state["kensho_theme"] = "light"

    is_dark = st.toggle(
        label,
        value=(st.session_state["kensho_theme"] == "dark"),
        key="_kensho_theme_switch",
        help="Switch between Light (default) and Dark mode",
    )
    st.session_state["kensho_theme"] = "dark" if is_dark else "light"
    return st.session_state["kensho_theme"]


def _find_file(dirs: list[Path], name: str, exts: tuple[str, ...] = ("",)) -> Path | None:
    for d in dirs:
        if not d.is_dir():
            continue
        for p in d.iterdir():
            for ext in exts:
                if p.is_file() and p.name.lower() == (name + ext).lower():
                    return p
    return None


@st.cache_data(show_spinner=False)
def _data_uri(path_str: str) -> str:
    p = Path(path_str)
    mime = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(p.suffix.lower(), "image/png")
    return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode('ascii')}"


@st.cache_data(show_spinner=False)
def _font_face_css() -> str:
    mimes = {
        ".woff2": ("font/woff2", "woff2"),
        ".woff": ("font/woff", "woff"),
        ".otf": ("font/otf", "opentype"),
        ".ttf": ("font/ttf", "truetype"),
    }
    out = []
    for family, faces in LOCAL_FONTS.items():
        for stem, weight, style in faces:
            f = _find_file(FONT_DIRS, stem, tuple(mimes))
            if not f:
                continue
            mime, fmt = mimes[f.suffix.lower()]
            b64 = base64.b64encode(f.read_bytes()).decode("ascii")
            out.append(
                f"@font-face {{ font-family: '{family}'; font-style: {style}; "
                f"font-weight: {weight}; src: url(data:{mime};base64,{b64}) format('{fmt}'); "
                f"font-display: swap; }}"
            )
    return "\n".join(out)


def missing_fonts() -> list[str]:
    exts = (".woff2", ".woff", ".otf", ".ttf")
    return [
        fam
        for fam, faces in LOCAL_FONTS.items()
        if not any(_find_file(FONT_DIRS, stem, exts) for stem, _, _ in faces)
    ]


def _root_vars(mode: str) -> str:
    def name(k: str) -> str:
        return k.replace("_", "-")

    def val(k: str, v, scale: float) -> str:
        return f"{round(v * scale, 2)}px" if k.startswith("fs_") else str(v)

    parts = [f"--{name(k)}: {v};" for k, v in PALETTE[mode].items()]
    parts += [f"--side-{name(k)}: {val(k, v, SIDEBAR_SCALE)};" for k, v in SIDEBAR.items()]
    parts += [f"--{name(k)}: {val(k, v, MAIN_SCALE)};" for k, v in MAIN.items()]
    parts += [f"--f-{k}: {v};" for k, v in FONTS.items()]
    return ":root {\n  " + "\n  ".join(parts) + "\n}"


# ---------------------------------------------------------------------------
# 6. STATIC CSS (cleanly grouped & commented; compacted at runtime by build_css)
# ---------------------------------------------------------------------------
_STATIC_CSS = """
/* ==========================================================================
   1. PAGE & MAIN CONTAINER
   - Background colour: --bg (#FFFFFF light / #121212 dark)
   - Watermark image: sized to --bg-w (min(46vw, 72vh)), centred at --bg-cx (55% of main)
   - Main padding: 3rem top, 3.5rem left & right
   ========================================================================== */
html, body {
    background: var(--bg) !important;
    color: var(--text);
}

.stApp {
    background-color: var(--bg) !important;
    background-repeat: no-repeat;
    background-attachment: fixed;
    background-size: var(--bg-w) auto;
    background-position:
        calc(var(--side-width) + (100vw - var(--side-width)) * var(--bg-cx) - var(--bg-w) / 2)
        var(--bg-y);
}

[data-testid="stAppViewContainer"],
[data-testid="stMain"],
[data-testid="stMainBlockContainer"],
[data-testid="stHeader"] {
    background: transparent !important;
}

/* Keep top-right Deploy button & 3-dots menu legible in both Light & Dark modes */
[data-testid="stHeader"] button,
[data-testid="stToolbar"] button,
[data-testid="stToolbar"] svg {
    color: var(--text) !important;
    fill: var(--text) !important;
}

[data-testid="stMain"] .block-container {
    padding-top: 3rem;        /* 48px breathing room above KENSHO masthead */
    padding-left: 3.5rem;     /* 56px left gutter */
    padding-right: 3.5rem;    /* 56px right gutter */
    max-width: 100%;
}

/* Default main-area typography: Google Sans 16px, letter-spacing 0.4px */
[data-testid="stMain"] p,
[data-testid="stMain"] li,
[data-testid="stMain"] label,
[data-testid="stMain"] [data-testid="stMarkdownContainer"] {
    font-family: var(--f-result);
    font-size: var(--fs-body);
    letter-spacing: var(--ls-body);
    color: var(--text);
}


/* ==========================================================================
   2. SIDEBAR LAYOUT & TYPOGRAPHY
   - Width: --side-width (33.333vw)
   - Surface: --side-bg (#2A2A2A light / #1E1E1E dark), text: --side-text
   - Padding: 0.4rem top, 1.6rem left/right, 1rem bottom
   - Vertical widget gap: 0.4rem
   ========================================================================== */
section[data-testid="stSidebar"] {
    background: var(--side-bg) !important;
}

section[data-testid="stSidebar"][aria-expanded="true"] {
    width: var(--side-width) !important;
    min-width: var(--side-width) !important;
    max-width: var(--side-width) !important;
}

[data-testid="stSidebarHeader"] {
    padding: 0.5rem 1rem 0 1.5rem;
    height: auto;
}

[data-testid="stSidebarContent"] {
    scrollbar-width: none;
}
[data-testid="stSidebarContent"]::-webkit-scrollbar {
    display: none;
}

[data-testid="stSidebarUserContent"] {
    padding: var(--side-pad-top) var(--side-pad-x) 1rem var(--side-pad-x) !important;
}

[data-testid="stSidebar"] [data-testid="stVerticalBlock"] {
    gap: var(--side-gap);
}

[data-testid="stSidebar"] svg {
    color: var(--side-text);
    fill: var(--side-text);
}

/* Sidebar "Target" title: MADE Mirage, bold (700), 28px, line-height 1.1 */
.k-side-title {
    font-family: var(--f-display);
    font-weight: 1000;
    font-size: var(--side-fs-title);
    line-height: 1.1;
    color: var(--side-text);
    margin: 0.1rem 0 1.2rem 0;
}

/* Sidebar field labels: Apparatus SIL, italic, 17px, line-height 1.25 */
[data-testid="stSidebar"] [data-testid="stWidgetLabel"] {
    margin-bottom: 0;
    min-height: 0;
}
[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p {
    font-family: var(--f-label) !important;
    font-style: italic;
    font-weight: 400;
    font-size: var(--side-fs-label) !important;
    line-height: 1.25;
    letter-spacing: 0;
    color: var(--side-text) !important;
}

/* Hide the << collapse and >> expand buttons */
[data-testid="stSidebarCollapseButton"],
[data-testid="stExpandSidebarButton"],
[data-testid="collapsedControl"] {
    display: none !important;
}

/* Lock sidebar open at --side-width (replaces the [aria-expanded="true"] rule) */
section[data-testid="stSidebar"] {
    background: var(--side-bg) !important;
    width: var(--side-width) !important;
    min-width: var(--side-width) !important;
    max-width: var(--side-width) !important;
    transform: none !important;
    margin-left: 0 !important;
}

/* ==========================================================================
   3. SIDEBAR INPUTS, SELECTBOX, SLIDER & RUN BUTTON
   - Input box height: --side-input-h (2.6rem), radius: 8px
   - Text/Number fill: --input-bg (#4A4A4A), border: 1px solid --input-border (#6B6B6B)
   - Text/Number text: DM Sans, light (300), 15px, --side-text (#FFFFFF)
   - Selectbox (Item Type): light box with black (#000000) text & chevron
   ========================================================================== */
[data-testid="stSidebar"] [data-testid="stTextInputRootElement"],
[data-testid="stSidebar"] [data-baseweb="input"],
[data-testid="stSidebar"] [data-testid="stNumberInputContainer"] {
    background: var(--input-bg) !important;
    border: 1px solid var(--input-border) !important;
    border-radius: var(--side-radius) !important;
    min-height: var(--side-input-h);
}

[data-testid="stSidebar"] [data-baseweb="base-input"],
[data-testid="stSidebar"] input {
    background: transparent !important;
}

/* Text & number inputs: white text */
[data-testid="stSidebar"] input {
    font-family: var(--f-ui) !important;
    font-weight: 300 !important;
    font-size: var(--side-fs-input) !important;
    color: var(--side-text) !important;
    -webkit-text-fill-color: var(--side-text) !important;
}

/* Item Type selectbox: black text & chevron on light box */
[data-testid="stSidebar"] [data-baseweb="select"] > div {
    border: 1px solid var(--input-border) !important;
    border-radius: var(--side-radius) !important;
    min-height: var(--side-input-h);
}
[data-testid="stSidebar"] [data-baseweb="select"] div,
[data-testid="stSidebar"] [data-baseweb="select"] span {
    font-family: var(--f-ui) !important;
    font-weight: 300 !important;
    font-size: var(--side-fs-input) !important;
    color: #000000 !important;
    -webkit-text-fill-color: #000000 !important;
}
[data-testid="stSidebar"] [data-baseweb="select"] svg {
    color: #000000 !important;
    fill: #000000 !important;
}

[data-testid="stSidebar"] [data-testid="stNumberInput"] button {
    background: transparent !important;
    color: var(--side-text) !important;
    border: none !important;
    display: none !important;
}

/* Slider value readout: DM Sans 15px, --blue */
[data-testid="stSidebar"] [data-testid="stSlider"] {
    padding-bottom: 0.1rem;
}
[data-testid="stSidebar"] [data-testid="stSlider"] [data-testid="stSliderThumbValue"] {
    font-family: var(--f-ui);
    font-size: var(--side-fs-slider);
    font-weight: 400;
    color: var(--blue) !important;
}
[data-testid="stSidebar"] [data-testid="stSliderTickBar"] {
    display: none;
}

/* Run button: height 2.8rem, radius 8px, fill --btn-bg (#3E7AD3), DM Sans 16px */
[data-testid="stSidebar"] [data-testid="stBaseButton-primary"],
[data-testid="stSidebar"] .stButton > button {
    background: var(--btn-bg) !important;
    color: var(--btn-text) !important;
    border: none !important;
    border-radius: var(--side-radius) !important;
    min-height: var(--side-button-h);
}
[data-testid="stSidebar"] .stButton > button p {
    font-family: var(--f-ui) !important;
    font-weight: 400;
    font-size: var(--side-fs-button) !important;
    color: var(--btn-text) !important;
}

/* Sidebar bottom metadata block: Gotham light, 12.5px, line-height 1.35 */
.k-meta {
    margin-top: 0.3rem;
    padding-top: 0.6rem;
    border-top: 1px solid var(--input-border);
    font-family: var(--f-meta);
    font-weight: 300;
    font-size: var(--side-fs-meta);
    color: var(--side-text);
}
.k-meta-row {
    display: flex;
    justify-content: space-between;
    gap: 10px;
    padding: 1px 0;
    line-height: 1.35;
}
.k-meta-row span {
    opacity: 0.65;
    white-space: nowrap;
}
.k-meta-row b {
    font-weight: 400;
    text-align: right;
    overflow-wrap: anywhere;
}
.k-meta-warn {
    color: #FBBF24;
    margin-top: 4px;
}
.k-meta code {
    font-size: inherit;
    background: transparent;
    color: inherit;
}


/* ==========================================================================
   4. MASTHEAD ("KENSHŌ" + VERSION BADGE + SUBTITLE)
   - Wordmark: MADE Mirage 72px, letter-spacing 8.6px, line-height 1.05, --blue
   - Macron over O: top 0.06em, thickness 0.055em
   - Badge: FreeSans 12px, letter-spacing 1.44px, pill radius 999px, padding 1px 10px
   - Subtitle: Palo light 15px, letter-spacing 1.12px, --text
   ========================================================================== */
.k-mast {
    margin: 0 0 1.2rem 0;
}
.k-title {
    font-family: var(--f-display);
    font-weight: 400;
    font-size: var(--fs-title);
    letter-spacing: var(--ls-title);
    line-height: 1.05;
    color: var(--blue);
    margin: 0;
    display: inline-block;
}
.k-title .k-o {
    position: relative;
    display: inline-block;
}
.k-title .k-o::before {
    content: "";
    position: absolute;
    top: 0.06em;
    height: 0.055em;
    left: 0.06em;
    right: calc(var(--ls-title) + 0.06em);
    background: var(--blue);
}
.k-badge {
    font-family: var(--f-badge);
    font-size: var(--fs-badge);
    letter-spacing: var(--ls-badge);
    line-height: 1.5;
    color: var(--blue-alt);
    background: var(--blue-tint);
    border: 1px solid var(--blue);
    border-radius: 999px;
    padding: 1.3px 5px;
    margin-left: 6px;
    vertical-align: 0.6rem;
    display: inline-block;
}
.k-sub {
    font-family: var(--f-sub);
    font-weight: 300;
    font-size: var(--fs-sub);
    letter-spacing: var(--ls-sub);
    color: var(--text);
    margin: 0.4rem 0 0 0.3rem;
}


/* ==========================================================================
   5. RESULTS: CAPTION, STATUS BANNER & METRICS ROW
   - Caption: Google Sans 16px, letter-spacing 0.4px, --text2
   - Banner: radius 10px, padding 1rem 1.5rem; title 17px, body 14px
   - Metrics grid: 4 equal columns, 1rem gap; label 15px, value 34px
   ========================================================================== */
.k-caption {
    font-family: var(--f-result);
    font-size: var(--fs-caption);
    letter-spacing: var(--ls-caption);
    color: var(--text2);
    margin: 1.4rem 0 0.9rem 0;
}

.k-banner {
    border: 1px solid var(--green);
    background: var(--green-tint);
    color: var(--green-text);
    border-radius: 10px;
    padding: 1rem 1.5rem;
    font-family: var(--f-result);
    letter-spacing: 0.4px;
}
.k-banner.warn {
    border-color: var(--warn);
    background: var(--warn-tint);
    color: var(--warn);
}
.k-banner.err {
    border-color: var(--red);
    background: var(--red-tint);
    color: var(--red);
}
.k-banner-title {
    font-size: var(--fs-banner);
    font-weight: 400;
    line-height: 1.35;
}
.k-banner-body {
    font-size: var(--fs-banner-body);
    margin-top: 0.4rem;
    opacity: 0.9;
    line-height: 1.45;
}

.k-metrics {
    display: grid;
    grid-template-columns: 1.4fr 1.1fr 0.9fr 1fr;
    gap: 1.5rem;
    margin: 2rem 0 1.6rem 0;
    align-items: start;
}
.k-m-label {
    font-family: var(--f-result);
    font-size: var(--fs-metric-label);
    letter-spacing: 0.34px;
    color: var(--text2);
    text-transform: uppercase;
}
.k-m-value {
    font-family: var(--f-result);
    font-size: 26px;
    font-weight: 700;
    letter-spacing: var(--ls-metric);
    color: var(--text);
    line-height: 1.2;
    margin-top: 0.3rem;
    word-break: break-word;
}

/* ==========================================================================
   6. TABS & TAB CONTENT
   - Tab labels: Apparatus SIL italic, 17px, letter-spacing 0.4px, gap 2rem
   - Active tab text & underline: --red (#DC0000 light / #F87171 dark)
   - Divider line: 1px solid --divider (#D9D9D9 light / #3A3A3A dark)
   ========================================================================== */
[data-testid="stTabs"] [role="tablist"] {
    gap: 2rem;
    border-bottom: 1px solid var(--divider);
}
[data-testid="stSidebar"] [data-testid="stSelectbox"] *:not([data-testid="stWidgetLabel"]):not([data-testid="stWidgetLabel"] *) {
    color: #000000 !important;
    -webkit-text-fill-color: #000000 !important;
}
[data-testid="stSidebar"] [data-testid="stSelectbox"] svg {
    color: #000000 !important;
    fill: #000000 !important;
}
[data-testid="stTabs"] button[role="tab"] {
    background: transparent !important;
    padding-left: 0.4rem;
    padding-right: 0.4rem;
}
[data-testid="stTabs"] button[role="tab"] p,
[data-testid="stTabs"] button[role="tab"] div,
[data-testid="stTabs"] button[role="tab"] span {
    font-family: var(--f-label) !important;
    font-style: italic !important;
    font-weight: 400 !important;
    font-size: var(--fs-tab) !important;
    letter-spacing: 0.4px;
    color: var(--text) !important;
}
[data-testid="stTabs"] button[role="tab"][aria-selected="true"] p,
[data-testid="stTabs"] button[role="tab"][aria-selected="true"] div,
[data-testid="stTabs"] button[role="tab"][aria-selected="true"] span {
    color: var(--red) !important;
}

.k-line {
    font-family: var(--f-result);
    font-size: var(--fs-body);
    letter-spacing: var(--ls-body);
    color: var(--text);
    margin: 1rem 0 0.35rem 0;
}
.k-line b {
    font-weight: 700;
}
.k-list {
    margin: 0 0 0 1.4rem;
    padding: 0;
    font-family: var(--f-result);
    font-size: var(--fs-body);
    letter-spacing: var(--ls-body);
    color: var(--text);
}
.k-list li {
    margin: 0.15rem 0;
    overflow-wrap: anywhere;
}
.k-chip {
    display: inline-block;
    font-family: var(--f-result);
    font-size: 0.85em;
    padding: 1px 10px;
    margin: 2px 6px 2px 0;
    border: 1px solid var(--divider);
    border-radius: 999px;
    color: var(--text);
}
.k-pill {
    display: inline-block;
    padding: 1px 12px;
    border-radius: 999px;
    border: 1px solid var(--divider);
}
.k-pill.err {
    color: var(--red);
    border-color: var(--red);
    background: var(--red-tint);
}
.k-muted {
    color: var(--text2);
}

/* ==========================================================================
   7. CODE BLOCKS, EXPANDERS, STATUS & ALERTS
   - Border: 1px solid --divider, radius 8px (code/expander) or 10px (status/alert)
   - Code & expander header surface: --code-bg (#F3F4F6 light / #1E1E1E dark)
   - Text & icons forced to --text so Dark mode stays high-contrast
   ========================================================================== */
[data-testid="stCode"] pre,
[data-testid="stCode"] code {
    background: var(--code-bg) !important;
    color: var(--text) !important;
}
[data-testid="stCode"] .linenumber,
[data-testid="stCode"] .token.punctuation,
[data-testid="stCode"] .token.operator {
    color: var(--text2) !important;
}
[data-testid="stCode"] button {
    background: var(--code-bg) !important;
    color: var(--text) !important;
}
[data-testid="stCode"] button svg {
    color: var(--text) !important;
    fill: var(--text) !important;
}
[data-testid="stCode"] {
    border: 1px solid var(--divider);
    border-radius: 8px;
}

[data-testid="stExpander"] details {
    border: 1px solid var(--divider) !important;
    background: transparent !important;
    border-radius: 8px;
}
[data-testid="stExpander"] summary {
    background: var(--code-bg) !important;
    color: var(--text) !important;
    border-radius: 8px;
}
[data-testid="stExpander"] summary p,
[data-testid="stStatus"] summary p {
    font-family: var(--f-label);
    font-style: italic;
    font-size: var(--fs-body);
    color: var(--text) !important;
}
[data-testid="stExpander"] summary svg,
[data-testid="stStatus"] summary svg {
    color: var(--text) !important;
    fill: var(--text) !important;
}

[data-testid="stVerticalBlockBorderWrapper"] {
    border-color: var(--divider) !important;
}

[data-testid="stStatus"] details,
[data-testid="stAlert"] {
    border-color: var(--divider) !important;
    border-radius: 10px;
}
"""


def _compact_css(css: str) -> str:
    """Strip /* ... */ comments, collapse newlines/indentation so Streamlit Markdown won't mangle <style>."""
    no_comments = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return " ".join(line.strip() for line in no_comments.splitlines() if line.strip())


def build_css(mode: str | None = None) -> str:
    """Complete <style> block for the given mode ('light' or 'dark', defaults to current_mode())."""
    if mode is None:
        mode = current_mode()
    mode = mode if mode in PALETTE else "light"

    imports = "".join(f"@import url('{u}');" for u in GOOGLE_IMPORTS)
    bg_file = _find_file(ASSET_DIRS, f"kensho-bg-{mode}.png")
    bg_css = f'.stApp {{ background-image: url("{_data_uri(str(bg_file))}"); }}' if bg_file else ""

    raw_css = imports + _font_face_css() + _root_vars(mode) + _STATIC_CSS + bg_css
    return f"<style>{_compact_css(raw_css)}</style>"


# ---------------------------------------------------------------------------
# 7. HTML SNIPPETS (single-line so Streamlit markdown cannot mangle them)
# ---------------------------------------------------------------------------
def masthead_html(version: str) -> str:
    return (
        '<div class="k-mast">'
        f'<div><span class="k-title">KENSH<span class="k-o">O</span></span>'
        f'<span class="k-badge">(v{esc(version)})</span></div>'
        '<div class="k-sub">Autonomous PR Triage &amp; Sandboxed Bug Reproduction</div>'
        "</div>"
    )


def caption_html(text: str) -> str:
    return f'<div class="k-caption">{esc(text)}</div>'


def banner_html(kind: str, title: str, body: str = "") -> str:
    """kind: 'ok' (green) | 'warn' | 'err'."""
    cls = "" if kind == "ok" else kind
    body_html = f'<div class="k-banner-body">{esc(body)}</div>' if body else ""
    return f'<div class="k-banner {cls}"><div class="k-banner-title">{esc(title)}</div>{body_html}</div>'


def metrics_html(items: list[tuple[str, str]]) -> str:
    cells = "".join(
        f'<div><div class="k-m-label">{esc(label)}</div><div class="k-m-value">{esc(value)}</div></div>'
        for label, value in items
    )
    return f'<div class="k-metrics">{cells}</div>'


def sidebar_title_html(text: str) -> str:
    return f'<div class="k-side-title">{esc(text)}</div>'