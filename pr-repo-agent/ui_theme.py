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
    """Editorial Swiss-grid CSS in Cobalt/Sky Blue & Crisp White, supporting Light & Dark mode."""
    if light_mode:
        bg = "#F8FAFC"              # Crisp gallery off-white
        surface = "#FFFFFF"         # Pure white editorial card
        surface_tint = "#EFF6FF"    # Soft ice-blue tint
        ink = "#0B192C"             # Deep navy ink
        muted = "#475569"           # Slate secondary text
        accent = "#1D4ED8"          # Primary Cobalt Blue
        accent_soft = "#DBEAFE"     # Pale sky badge fill
        border = "#CBD5E1"          # Crisp hairline rule
    else:
        bg = "#070D19"              # Deep midnight navy
        surface = "#0D1627"         # Elevated slate-navy surface
        surface_tint = "#112240"    # Cobalt-tinted panel
        ink = "#F8FAFC"             # Crisp white ink
        muted = "#94A3B8"           # Cool silver-blue secondary
        accent = "#38BDF8"          # Luminous Sky Blue
        accent_soft = "#0C4A6E"     # Deep cerulean badge fill
        border = "#1E293B"          # Subtle navy hairline

    return f"""
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500;600&family=Playfair+Display:ital,wght@0,600;0,700;1,500&display=swap');

        .stApp {{
            background-color: {bg};
            color: {ink};
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
        }}

        /* Editorial Swiss-Grid Hero Masthead */
        .kensho-masthead {{
            display: grid;
            grid-template-columns: 1.35fr 1fr;
            gap: 24px;
            background: {surface};
            border: 1px solid {border};
            border-top: 4px solid {accent};
            border-radius: 6px;
            padding: 1.5rem 1.75rem;
            margin-bottom: 1.25rem;
        }}

        .kensho-brand-row {{
            display: flex;
            align-items: center;
            gap: 14px;
            margin-bottom: 0.75rem;
        }}

        .kensho-brand-row img {{
            width: 46px;
            height: 46px;
            border-radius: 8px;
            object-fit: contain;
            background: {surface_tint};
            padding: 4px;
            border: 1px solid {border};
        }}

        .kensho-kicker {{
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.72rem;
            letter-spacing: 0.12em;
            text-transform: uppercase;
            color: {accent};
            font-weight: 600;
            margin: 0;
        }}

        .kensho-display-title {{
            font-family: 'Playfair Display', Georgia, serif;
            font-size: 1.85rem;
            font-weight: 700;
            line-height: 1.15;
            color: {ink};
            margin: 0.35rem 0 0.4rem 0;
            letter-spacing: -0.01em;
        }}

        .kensho-tagline {{
            font-size: 0.9rem;
            color: {muted};
            line-height: 1.5;
            margin: 0;
            max-width: 46ch;
        }}

        /* Right-hand numbered Process rail inspired by editorial poster */
        .kensho-process-box {{
            border-left: 1px solid {border};
            padding-left: 1.5rem;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
        }}

        .kensho-process-heading {{
            font-family: 'Playfair Display', Georgia, serif;
            font-size: 1.25rem;
            font-weight: 600;
            color: {ink};
            margin: 0 0 0.6rem 0;
        }}

        .kensho-step-list {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 8px 16px;
        }}

        .kensho-step {{
            border-top: 1px solid {border};
            padding-top: 6px;
        }}

        .kensho-step-num {{
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.68rem;
            color: {accent};
            font-weight: 600;
        }}

        .kensho-step-name {{
            font-size: 0.8rem;
            font-weight: 600;
            color: {ink};
        }}

        /* Metric cards & Tabs polish */
        div[data-testid="stMetric"] {{
            background: {surface};
            border: 1px solid {border};
            border-left: 3px solid {accent};
            border-radius: 6px;
            padding: 0.75rem 1rem;
        }}

        button[data-baseweb="tab"] {{
            font-family: 'JetBrains Mono', monospace !important;
            font-size: 0.8rem !important;
            letter-spacing: 0.03em;
        }}

        @media (max-width: 820px) {{
            .kensho-masthead {{
                grid-template-columns: 1fr;
            }}
            .kensho-process-box {{
                border-left: none;
                border-top: 1px solid {border};
                padding-left: 0;
                padding-top: 1rem;
            }}
        }}
    </style>
    """


def brand_header_html(version: str) -> str:
    """Renders the asymmetric editorial blue-and-white Kensho masthead."""
    logo_path = find_logo()
    img_tag = ""
    if logo_path:
        b64 = base64.b64encode(logo_path.read_bytes()).decode("ascii")
        img_tag = f'<img src="data:image/png;base64,{b64}" alt="Kensho Logo" />'

    return f"""
    <div class="kensho-masthead">
        <div>
            <div class="kensho-brand-row">
                {img_tag}
                <div>
                    <p class="kensho-kicker">KENSHŌ · {version}</p>
                    <span style="font-family:'JetBrains Mono',monospace;font-size:0.72rem;opacity:0.75;">
                        FORK-AWARE REPRODUCTION ENGINE
                    </span>
                </div>
            </div>
            <h1 class="kensho-display-title">Seeing into root cause with clarity &amp; proof.</h1>
            <p class="kensho-tagline">
                Localizes defects across your repository map, synthesizes targeted tests, and verifies
                reproducibility inside ephemeral Docker sandboxes.
            </p>
        </div>
        <div class="kensho-process-box">
            <p class="kensho-process-heading">Process</p>
            <div class="kensho-step-list">
                <div class="kensho-step">
                    <div class="kensho-step-num">01 / TRIAGE</div>
                    <div class="kensho-step-name">Map &amp; Localize</div>
                </div>
                <div class="kensho-step">
                    <div class="kensho-step-num">02 / ARCHITECT</div>
                    <div class="kensho-step-name">Draft Repro Test</div>
                </div>
                <div class="kensho-step">
                    <div class="kensho-step-num">03 / SANDBOX</div>
                    <div class="kensho-step-name">Isolated Execution</div>
                </div>
                <div class="kensho-step">
                    <div class="kensho-step-num">04 / VERDICT</div>
                    <div class="kensho-step-name">Guarded Proof</div>
                </div>
            </div>
        </div>
    </div>
    """