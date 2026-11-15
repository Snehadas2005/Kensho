import base64
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def find_logo() -> Path | None:
    """Finds kensho-logo.png inside pr-repo-agent/assets or parent repo assets/."""
    candidates = [
        BASE_DIR / "assets" / "kensho-logo.png",
        BASE_DIR.parent / "assets" / "kensho-logo.png",
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def build_css(light_mode: bool = False) -> str:
    """Returns custom CSS for dark or light mode in the Kensho Streamlit UI."""
    if light_mode:
        bg, fg, card_bg, border, muted = "#ffffff", "#0f172a", "#f8fafc", "#e2e8f0", "#475569"
    else:
        bg, fg, card_bg, border, muted = "#0b0f19", "#f1f5f9", "#111827", "#1f2937", "#94a3b8"

    return f"""
    <style>
        .stApp {{
            background-color: {bg};
            color: {fg};
        }}
        .kensho-header {{
            display: flex;
            align-items: center;
            gap: 14px;
            padding: 0.75rem 1rem;
            margin-bottom: 1rem;
            background: {card_bg};
            border: 1px solid {border};
            border-radius: 12px;
        }}
        .kensho-header img {{
            width: 44px;
            height: 44px;
            border-radius: 8px;
            object-fit: contain;
        }}
        .kensho-title {{
            font-size: 1.45rem;
            font-weight: 700;
            margin: 0;
            line-height: 1.2;
        }}
        .kensho-badge {{
            font-size: 0.75rem;
            font-weight: 600;
            padding: 2px 8px;
            border-radius: 999px;
            background: #2563eb22;
            color: #60a5fa;
            border: 1px solid #3b82f644;
            margin-left: 8px;
            vertical-align: middle;
        }}
        .kensho-sub {{
            font-size: 0.85rem;
            color: {muted};
            margin: 2px 0 0 0;
        }}
    </style>
    """


def brand_header_html(version: str) -> str:
    """Renders the branded Kensho header banner with embedded base64 logo."""
    logo_path = find_logo()
    img_tag = ""
    if logo_path:
        b64 = base64.b64encode(logo_path.read_bytes()).decode("ascii")
        img_tag = f'<img src="data:image/png;base64,{b64}" alt="Kensho Logo" />'

    return f"""
    <div class="kensho-header">
        {img_tag}
        <div>
            <p class="kensho-title">Kensho <span class="kensho-badge">{version}</span></p>
            <p class="kensho-sub">Autonomous PR Triage &amp; Sandboxed Bug Reproduction</p>
        </div>
    </div>
    """