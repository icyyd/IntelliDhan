from pathlib import Path
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / "web" / "assets" / "brand"


def test_brand_svg_stack_is_well_formed_and_complete():
    expected = {
        "favicon.svg",
        "intellidhan-lockup.svg",
        "intellidhan-mark-inverse.svg",
        "intellidhan-mark.svg",
        "social-card.svg",
    }
    assert expected <= {path.name for path in BRAND.glob("*.svg")}
    for name in expected:
        ElementTree.parse(BRAND / name)


def test_approved_mark_uses_orange_bindu_and_no_retired_dot_colors():
    for name in ("intellidhan-mark.svg", "intellidhan-mark-inverse.svg", "favicon.svg"):
        source = (BRAND / name).read_text(encoding="utf-8").upper()
        assert "#E56F2D" in source
        assert "#3D63DD" not in source
        assert "#2DD4BF" not in source


def test_web_shell_loads_brand_assets_and_fonts():
    source = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert 'href="/assets/brand/favicon.svg"' in source
    assert 'href="/assets/brand/intellidhan-theme.css"' in source
    assert 'src="/assets/brand/intellidhan-mark-inverse.svg"' in source
    assert 'src="/assets/brand/intellidhan-mark.svg"' in source
    assert "Newsreader" in source and "Manrope" in source and "JetBrains+Mono" in source


def test_gateway_mounts_the_static_asset_directory():
    source = (ROOT / "services/gateway/intellidhan_gateway/app.py").read_text(
        encoding="utf-8"
    )
    assert 'app.mount("/assets", StaticFiles(directory=WEB_DIR / "assets")' in source


def test_brand_theme_keeps_semantic_market_colors_distinct():
    source = (BRAND / "intellidhan-theme.css").read_text(encoding="utf-8")
    assert "--brand-orange:#E56F2D" in source
    assert "--cyan:var(--brand-orange)" in source
    assert "--brand-gain:#4AC39B" in source
    assert "--brand-loss:#EF6A78" in source
    assert "color alone" in (
        ROOT / "design-system" / "intellidhan" / "MASTER.md"
    ).read_text(encoding="utf-8")
