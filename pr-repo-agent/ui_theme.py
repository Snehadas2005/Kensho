"""
Kensho v2 - UI theme
====================
Everything you may want to customise lives in the three blocks at the top:

    1. PALETTE  - every colour, for light and dark mode
    2. SIZES    - every font size / letter-spacing / layout width
    3. FONTS    - which font family is used for which role

Light mode is the default. The mode follows Streamlit's built-in theme switch
(the "Settings" entry in the 3-dots menu at the top-right), read through
`st.context.theme.type` (needs streamlit >= 1.46).

Font files and background images are searched in:
    assets/            kensho-bg-light.png, kensho-bg-dark.png
    assets/fonts/      *.ttf / *.otf / *.woff / *.woff2   (see LOCAL_FONTS)
"""
from __future__ import annotations

import base64
import html
from pathlib import Path

import streamlit as st

BASE_DIR = Path(__file__).resolve().parent
ASSET_DIRS = [BASE_DIR / "assets", BASE_DIR, BASE_DIR.parent / "assets", BASE_DIR.parent]
FONT_DIRS = [BASE_DIR / "assets" / "fonts", BASE_DIR / "fonts", BASE_DIR.parent / "assets" / "fonts"]

# ---------------------------------------------------------------------------
# 1. PALETTE  (edit any hex / rgba value; keys become CSS variables)
# ---------------------------------------------------------------------------
PALETTE = {
    "light": {
        "bg": "#FFFFFF",                      # page background
        "side_bg": "#2A2A2A",                 # sidebar background (card / container)
        "side_text": "#FFFFFF",               # sidebar text + icons
        "input_bg": "rgba(255,255,255,0.14)",  # sidebar input fill
        "input_border": "rgba(217,217,217,0.35)",  # sidebar input border
        "text": "#000000",                    # primary text
        "text2": "#676767",                   # secondary text (captions)
        "muted": "#939393",                   # muted text
        "blue": "#4186EC",                    # primary blue (masthead, slider value)
        "blue_alt": "#3E7AD3",                # primary blue (version badge text)
        "blue_tint": "rgba(174,206,255,0.74)",  # badge / selection tint (74%)
        "btn_bg": "#3E7AD3",                  # Run button fill  (alt blue: #4082E4)
        "btn_text": "#FFFFFF",                # Run button text
        "red": "#DC0000",                     # active tab, errors
        "red_tint": "rgba(220,0,0,0.12)",     # error banner fill
        "green": "#00A220",                   # success border
        "green_text": "#016802",              # success text
        "green_tint": "rgba(1,176,36,0.40)",  # success banner fill (40%)
        "warn": "#B45309",                    # NOT REPRODUCED text (not in your palette - edit freely)
        "warn_tint": "rgba(245,158,11,0.25)",  # NOT REPRODUCED fill
        "divider": "#D9D9D9",                 # borders / dividers
        "code_bg": "#F3F4F6",                 # code block background
    },
    "dark": {
        "bg": "#121212",
        "side_bg": "#1E1E1E",
        "side_text": "#F3F4F6",
        "input_bg": "rgba(255,255,255,0.08)",
        "input_border": "rgba(255,255,255,0.12)",
        "text": "#F3F4F6",
        "text2": "#A3A3A3",
        "muted": "#737373",
        "blue": "#5B9BF8",
        "blue_alt": "#6CA6FA",
        "blue_tint": "rgba(59,130,246,0.25)",
        "btn_bg": "#5B9BF8",
        "btn_text": "#0B1220",
        "red": "#F87171",
        "red_tint": "rgba(248,113,113,0.18)",
        "green": "#10B981",
        "green_text": "#34D399",
        "green_tint": "rgba(16,185,129,0.22)",
        "warn": "#FBBF24",
        "warn_tint": "rgba(251,191,36,0.18)",
        "divider": "rgba(255,255,255,0.12)",
        "code_bg": "#1E1E1E",
    },
}

# ---------------------------------------------------------------------------
# 2. SIZES  (values copied from your design spec; change freely)
# ---------------------------------------------------------------------------
SIZES = {
    # layout
    "sidebar_w": "33.333vw",     # sidebar = 1/3 of the screen
    "bg_size": "min(46vw, 72vh)",  # size of the Kensho background mark
    "bg_pos": "62% 62%",         # position of the background mark
    # masthead
    "fs_title": "80px",          # KENSHO
    "ls_title": "9.6px",         # KENSHO letter-spacing
    "fs_badge": "12px",          # (v1.0.0)
    "ls_badge": "1.44px",
    "fs_sub": "16px",            # Autonomous PR Triage & ...
    "ls_sub": "1.12px",
    # sidebar
    "fs_side_title": "35px",     # "Target"
    "fs_side_label": "25px",     # italic labels
    "fs_side_input": "20px",     # input values
    "fs_side_button": "20px",    # Run button
    "fs_side_meta": "15px",      # username / repo map / sandbox image block
    # main area
    "fs_caption": "20px",        # "Showing your last saved run..."
    "ls_caption": "0.4px",
    "fs_banner": "20px",         # green box
    "fs_banner_body": "16px",    # issue description excerpt in the green box
    "fs_metric_label": "17px",   # Status / Runtime / Attempts / Sandbox time
    "fs_metric_value": "38px",   # VERIFIED / PYTHON / 1 / 19.26s
    "ls_metric": "0.76px",
    "fs_tab": "20px",            # Triage / Test / Sandbox / Report
    "fs_body": "20px",           # tab content
    "ls_body": "0.4px",
}

# ---------------------------------------------------------------------------
# 3. FONTS  (role -> CSS font stack; first family is the one from the design)
# ---------------------------------------------------------------------------
FONTS = {
    "display": "'MADE Mirage', 'Playfair Display', Georgia, serif",   # KENSHO, "Target"
    "label": "'Apparatus SIL', Georgia, serif",                        # italic labels + tabs
    "ui": "'DM Sans', 'Helvetica Neue', Arial, sans-serif",            # sidebar inputs / button
    "result": "'Google Sans', 'DM Sans', 'Helvetica Neue', Arial, sans-serif",  # results area
    "sub": "'Palo', 'DM Sans', Arial, sans-serif",                     # subtitle
    "badge": "'FreeSans', 'Helvetica Neue', Arial, sans-serif",        # version badge
    "meta": "'Gotham', 'DM Sans', Arial, sans-serif",                  # small sidebar info block
}

# Local font files. (file-name stem, weight, style). Extension can be
# .woff2 / .woff / .otf / .ttf, matching is case-insensitive.
LOCAL_FONTS = {
    "MADE Mirage": [("MADE-Mirage-Regular", 400, "normal"), ("MADE-Mirage-Medium", 500, "normal"),
                    ("MADE-Mirage-Bold", 700, "normal")],
    "Palo": [("Palo-Light", 300, "normal"), ("Palo", 300, "normal")],
    "Gotham": [("Gotham-Light", 300, "normal"), ("Gotham-Book", 400, "normal")],
    "Google Sans": [("GoogleSans-Regular", 400, "normal"), ("GoogleSans-Bold", 700, "normal")],
    "FreeSans": [("FreeSans", 400, "normal")],
    "Apparatus SIL": [("ApparatusSIL-Italic", 400, "italic"), ("ApparatusSIL-Regular", 400, "normal")],
    "DM Sans": [("DMSans-Light", 300, "normal"), ("DMSans-Regular", 400, "normal")],
}

# Remote fonts (each import is independent, so one failing does not break others)
GOOGLE_IMPORTS = [
    "https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500&display=swap",
    "https://fonts.googleapis.com/css2?family=Apparatus+SIL:ital@0;1&display=swap",
    "https://fonts.googleapis.com/css2?family=Apparatus+SIL&display=swap",
    "https://fonts.googleapis.com/css2?family=Google+Sans:wght@400;700&display=swap",
]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def esc(value) -> str:
    return html.escape(str(value if value is not None else ""))


def current_mode() -> str:
    """'light' (default) or 'dark', following Streamlit's own theme switch."""
    try:
        return "dark" if st.context.theme.type == "dark" else "light"
    except Exception:  # older Streamlit or no theme info
        return "light"


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
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(
        p.suffix.lower(), "image/png")
    return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode('ascii')}"


@st.cache_data(show_spinner=False)
def _font_face_css() -> str:
    mimes = {".woff2": ("font/woff2", "woff2"), ".woff": ("font/woff", "woff"),
             ".otf": ("font/otf", "opentype"), ".ttf": ("font/ttf", "truetype")}
    out = []
    for family, faces in LOCAL_FONTS.items():
        for stem, weight, style in faces:
            f = _find_file(FONT_DIRS, stem, tuple(mimes))
            if not f:
                continue
            mime, fmt = mimes[f.suffix.lower()]
            b64 = base64.b64encode(f.read_bytes()).decode("ascii")
            out.append(
                f"@font-face{{font-family:'{family}';font-style:{style};font-weight:{weight};"
                f"src:url(data:{mime};base64,{b64}) format('{fmt}');font-display:swap;}}"
            )
    return "".join(out)


def missing_fonts() -> list[str]:
    """Families from LOCAL_FONTS that have no file in assets/fonts (useful for debugging)."""
    exts = (".woff2", ".woff", ".otf", ".ttf")
    return [fam for fam, faces in LOCAL_FONTS.items()
            if not any(_find_file(FONT_DIRS, stem, exts) for stem, _, _ in faces)]


def _root_vars(mode: str) -> str:
    parts = [f"--{k.replace('_', '-')}:{v};" for k, v in PALETTE[mode].items()]
    parts += [f"--{k.replace('_', '-')}:{v};" for k, v in SIZES.items()]
    parts += [f"--f-{k}:{v};" for k, v in FONTS.items()]
    return ":root{" + "".join(parts) + "}"


_STATIC_CSS = """
html, body, .stApp { background: var(--bg) !important; color: var(--text); }
[data-testid="stHeader"] { background: transparent !important; }
[data-testid="stMain"] { background-repeat: no-repeat; background-attachment: fixed;
  background-position: var(--bg-pos); background-size: var(--bg-size); }
[data-testid="stMain"] .block-container { padding-top: 3rem; padding-left: 3.5rem; padding-right: 3.5rem; max-width: 100%; }
[data-testid="stMain"] p, [data-testid="stMain"] li, [data-testid="stMain"] label,
[data-testid="stMain"] [data-testid="stMarkdownContainer"] { font-family: var(--f-result); color: var(--text);
  font-size: var(--fs-body); letter-spacing: var(--ls-body); }

/* ---------- sidebar (1/3 width, no scrolling needed) ---------- */
section[data-testid="stSidebar"] { background: var(--side-bg) !important; }
section[data-testid="stSidebar"][aria-expanded="true"] { width: var(--sidebar-w) !important;
  min-width: var(--sidebar-w) !important; max-width: var(--sidebar-w) !important; }
[data-testid="stSidebarHeader"] { padding: 0.6rem 1rem 0 1.5rem; height: auto; }
[data-testid="stSidebarContent"] { scrollbar-width: none; }
[data-testid="stSidebarContent"]::-webkit-scrollbar { display: none; }
[data-testid="stSidebarUserContent"] { padding: 0 2rem 1rem 2rem !important; }
[data-testid="stSidebar"] [data-testid="stVerticalBlock"] { gap: 0.55rem; }
[data-testid="stSidebar"] svg { color: var(--side-text); fill: var(--side-text); }
[data-testid="stSidebar"] [data-testid="stWidgetLabel"] { margin-bottom: 0.1rem; }
[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p { font-family: var(--f-label) !important;
  font-style: italic; font-weight: 400; font-size: var(--fs-side-label) !important;
  color: var(--side-text) !important; letter-spacing: 0; }
.k-side-title { font-family: var(--f-display); font-weight: 700; font-size: var(--fs-side-title);
  color: var(--side-text); line-height: 1.1; margin: 0.2rem 0 0.4rem 0; }
[data-testid="stSidebar"] [data-baseweb="input"], [data-testid="stSidebar"] [data-baseweb="base-input"],
[data-testid="stSidebar"] [data-baseweb="select"] > div { background: var(--input-bg) !important;
  border: 1px solid var(--input-border) !important; border-radius: 8px !important; }
[data-testid="stSidebar"] input, [data-testid="stSidebar"] [data-baseweb="select"] div {
  font-family: var(--f-ui) !important; font-weight: 300 !important; font-size: var(--fs-side-input) !important;
  color: var(--side-text) !important; -webkit-text-fill-color: var(--side-text); }
[data-testid="stSidebar"] [data-testid="stNumberInput"] button { background: transparent !important;
  color: var(--side-text) !important; border: none !important; }
[data-testid="stSidebar"] [data-testid="stSlider"] [data-testid="stSliderThumbValue"] {
  color: var(--blue) !important; font-family: var(--f-ui); font-size: var(--fs-side-input); font-weight: 400; }
[data-testid="stSidebar"] [data-testid="stSliderTickBar"] { display: none; }
[data-testid="stSidebar"] [data-testid="stSlider"] { padding-bottom: 0.2rem; }
[data-testid="stSidebar"] [data-testid="stBaseButton-primary"], [data-testid="stSidebar"] .stButton > button {
  background: var(--btn-bg) !important; color: var(--btn-text) !important; border: none !important;
  border-radius: 8px !important; min-height: 3.2rem; }
[data-testid="stSidebar"] .stButton > button p { font-family: var(--f-ui) !important; font-weight: 400;
  font-size: var(--fs-side-button) !important; color: var(--btn-text) !important; }
.k-meta { margin-top: 0.4rem; padding-top: 0.7rem; border-top: 1px solid var(--input-border);
  font-family: var(--f-meta); font-weight: 300; font-size: var(--fs-side-meta); color: var(--side-text); }
.k-meta-row { display: flex; justify-content: space-between; gap: 12px; padding: 2px 0; line-height: 1.35; }
.k-meta-row span { opacity: 0.65; white-space: nowrap; }
.k-meta-row b { font-weight: 400; text-align: right; word-break: break-all; }
.k-meta-warn { color: #FBBF24; margin-top: 4px; }

/* ---------- masthead ---------- */
.k-mast { margin: 0 0 1.4rem 0; }
.k-title { font-family: var(--f-display); font-weight: 400; font-size: var(--fs-title);
  letter-spacing: var(--ls-title); color: var(--blue); line-height: 1.05; margin: 0; display: inline-block; }
.k-title .k-o { position: relative; display: inline-block; }
/* macron over the O. Tune top / height / left / right to taste */
.k-title .k-o::before { content: ""; position: absolute; top: 0.06em; height: 0.055em;
  left: 0.06em; right: calc(var(--ls-title) + 0.06em); background: var(--blue); }
.k-badge { font-family: var(--f-badge); font-size: var(--fs-badge); letter-spacing: var(--ls-badge);
  color: var(--blue-alt); background: var(--blue-tint); border: 1px solid var(--blue); border-radius: 999px;
  padding: 1px 10px; margin-left: 6px; vertical-align: 2.2em; display: inline-block; line-height: 1.5; }
.k-sub { font-family: var(--f-sub); font-weight: 300; font-size: var(--fs-sub); letter-spacing: var(--ls-sub);
  color: var(--text); margin: 0.4rem 0 0 0.3rem; }

/* ---------- results ---------- */
.k-caption { font-family: var(--f-result); font-size: var(--fs-caption); letter-spacing: var(--ls-caption);
  color: var(--text2); margin: 1.6rem 0 1rem 0; }
.k-banner { border: 1px solid var(--green); background: var(--green-tint); color: var(--green-text);
  border-radius: 10px; padding: 1.1rem 1.6rem; font-family: var(--f-result); letter-spacing: 0.4px; }
.k-banner.warn { border-color: var(--warn); background: var(--warn-tint); color: var(--warn); }
.k-banner.err { border-color: var(--red); background: var(--red-tint); color: var(--red); }
.k-banner-title { font-size: var(--fs-banner); font-weight: 400; line-height: 1.35; }
.k-banner-body { font-size: var(--fs-banner-body); margin-top: 0.45rem; opacity: 0.9; line-height: 1.45; }
.k-metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 1rem; margin: 2.2rem 0 1.8rem 0; }
.k-m-label { font-family: var(--f-result); font-size: var(--fs-metric-label); letter-spacing: 0.34px; color: var(--text); }
.k-m-value { font-family: var(--f-result); font-size: var(--fs-metric-value); letter-spacing: var(--ls-metric);
  color: var(--text); line-height: 1.3; margin-top: 0.5rem; text-transform: uppercase; white-space: nowrap; }

/* ---------- tabs ---------- */
[data-baseweb="tab-list"] { gap: 2.2rem; }
[data-baseweb="tab-border"] { background-color: var(--divider) !important; }
[data-baseweb="tab-highlight"] { background-color: var(--red) !important; }
button[data-baseweb="tab"] { background: transparent !important; padding-left: 0.4rem; padding-right: 0.4rem; }
button[data-baseweb="tab"] p { font-family: var(--f-label) !important; font-style: italic; font-weight: 400;
  font-size: var(--fs-tab) !important; letter-spacing: 0.4px; color: var(--text) !important; }
button[data-baseweb="tab"][aria-selected="true"] p { color: var(--red) !important; }
.k-line { font-family: var(--f-result); font-size: var(--fs-body); letter-spacing: var(--ls-body);
  color: var(--text); margin: 1.1rem 0 0.4rem 0; }
.k-line b { font-weight: 700; }
.k-list { margin: 0 0 0 1.4rem; padding: 0; font-family: var(--f-result); font-size: var(--fs-body);
  letter-spacing: var(--ls-body); color: var(--text); }
.k-list li { margin: 0.15rem 0; word-break: break-all; }
.k-chip { display: inline-block; font-family: var(--f-result); font-size: 0.8em; padding: 1px 10px;
  margin: 2px 6px 2px 0; border: 1px solid var(--divider); border-radius: 999px; color: var(--text); }
.k-pill { display: inline-block; padding: 1px 12px; border-radius: 999px; border: 1px solid var(--divider); }
.k-pill.err { color: var(--red); border-color: var(--red); background: var(--red-tint); }
.k-muted { color: var(--text2); }

/* ---------- code, expanders, containers ---------- */
[data-testid="stCode"] pre, [data-testid="stCode"] code { background: var(--code-bg) !important; }
[data-testid="stCode"] { border: 1px solid var(--divider); border-radius: 8px; }
[data-testid="stExpander"] details { border: 1px solid var(--divider) !important; background: transparent !important;
  border-radius: 8px; }
[data-testid="stExpander"] summary p { font-family: var(--f-label); font-style: italic; font-size: var(--fs-body); }
[data-testid="stVerticalBlockBorderWrapper"] { border-color: var(--divider) !important; }
[data-testid="stStatus"] details, [data-testid="stAlert"] { border-color: var(--divider) !important; border-radius: 10px; }
"""


def build_css(mode: str = "light") -> str:
    """Complete <style> block for the given mode ('light' or 'dark')."""
    mode = mode if mode in PALETTE else "light"
    imports = "".join(f"@import url('{u}');" for u in GOOGLE_IMPORTS)

    bg_file = _find_file(ASSET_DIRS, f"kensho-bg-{mode}.png")
    bg_css = ""
    if bg_file:
        bg_css = f'[data-testid="stMain"]{{background-image:url("{_data_uri(str(bg_file))}");}}'

    css = imports + _font_face_css() + _root_vars(mode) + _STATIC_CSS + bg_css
    # one line per rule: Streamlit's markdown parser breaks on blank / indented lines
    css = "\n".join(line.strip() for line in css.splitlines() if line.strip())
    return f"<style>{css}</style>"


# ---------------------------------------------------------------------------
# HTML snippets (always single-line, so markdown cannot mangle them)
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