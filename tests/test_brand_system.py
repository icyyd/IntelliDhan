import hashlib
import re
import struct
from pathlib import Path
from xml.etree import ElementTree

from fastapi.testclient import TestClient

from intellidhan_gateway.app import app


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


def test_gateway_serves_brand_assets_with_safe_static_contract():
    client = TestClient(app)
    try:
        expected = {
            "/assets/brand/favicon.svg": "image/svg+xml",
            "/assets/brand/intellidhan-theme.css": "text/css",
            "/assets/brand/social-card.png": "image/png",
        }
        for url, media_type in expected.items():
            response = client.get(url)
            assert response.status_code == 200
            assert response.headers["content-type"].startswith(media_type)
            assert response.headers.get("etag")

        favicon = client.get("/assets/brand/favicon.svg")
        cached = client.get(
            "/assets/brand/favicon.svg",
            headers={"If-None-Match": favicon.headers["etag"]},
        )
        assert cached.status_code == 304
        assert client.get("/assets/%2e%2e/index.html").status_code == 404
    finally:
        client.close()


def test_brand_theme_keeps_semantic_market_colors_distinct():
    source = (BRAND / "intellidhan-theme.css").read_text(encoding="utf-8")
    assert "--brand-orange:#E56F2D" in source
    assert "--cyan:var(--brand-orange)" in source
    assert "--brand-gain:#4AC39B" in source
    assert "--brand-loss:#EF6A78" in source
    assert "color alone" in (
        ROOT / "design-system" / "intellidhan" / "MASTER.md"
    ).read_text(encoding="utf-8")


def test_d42_owl_lotus_geometry_is_shared_across_brand_assets():
    assets = (
        "intellidhan-mark.svg",
        "intellidhan-mark-inverse.svg",
        "favicon.svg",
        "intellidhan-lockup.svg",
        "social-card.svg",
    )
    required_geometry = (
        'd="M128 17C72 17 33 56 33 109v40c0 54 39 90 95 90s95-36 95-90v-40c0-53-39-92-95-92Z"',
        'd="M68 63c14 15 39 10 60 51"',
        'd="M188 63c-14 15-39 10-60 51"',
        'd="M77 69c-24 20-22 53 17 70"',
        'd="M179 69c24 20 22 53-17 70"',
        'd="M128 155c-18 19-23 43 0 67 23-24 18-48 0-67Z"',
        'd="M118 222c-35 1-55-20-58-54 29 0 51 19 58 54Z"',
        'd="M138 222c35 1 55-20 58-54-29 0-51 19-58 54Z"',
    )
    for name in assets:
        source = (BRAND / name).read_text(encoding="utf-8")
        assert all(path in source for path in required_geometry)
        assert 'd="M86 47h48c49 0 81 32 81 80' not in source
        assert 'cx="91" cy="101"' in source
        assert 'cx="165" cy="101"' in source
        assert 'cx="128" cy="218"' in source


def test_external_brand_artwork_uses_outlined_text_and_exact_share_dimensions():
    for name in ("intellidhan-lockup.svg", "social-card.svg"):
        source = (BRAND / name).read_text(encoding="utf-8")
        assert "<text" not in source
        assert 'data-outlined-text="Intelli"' in source
        assert 'data-outlined-text="Dhan"' in source

    png = (BRAND / "social-card.png").read_bytes()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", png[16:24]) == (1200, 630)
    assert hashlib.sha256(png).hexdigest() == (
        "62a51ab4cafca3913d8c8c7d959a45fca5b8cb7c70a5d502966511089c7447be"
    )


def _relative_luminance(hex_color: str) -> float:
    channels = [int(hex_color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [
        value / 12.92
        if value <= 0.04045
        else ((value + 0.055) / 1.055) ** 2.4
        for value in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast_ratio(foreground: str, background: str) -> float:
    lighter, darker = sorted(
        (_relative_luminance(foreground), _relative_luminance(background)),
        reverse=True,
    )
    return (lighter + 0.05) / (darker + 0.05)


def _blend(foreground: str, alpha: float, background: str) -> str:
    front = [int(foreground[index : index + 2], 16) for index in (1, 3, 5)]
    back = [int(background[index : index + 2], 16) for index in (1, 3, 5)]
    channels = [
        round(alpha * front_value + (1 - alpha) * back_value)
        for front_value, back_value in zip(front, back)
    ]
    return "#" + "".join(f"{channel:02X}" for channel in channels)


def test_light_theme_small_text_tokens_meet_aa_on_card_and_badge_surfaces():
    source = (BRAND / "intellidhan-theme.css").read_text(encoding="utf-8")
    light = re.search(r':root\[data-theme="light"\]\{(.*?)\n\}', source, re.S)
    assert light
    tokens = dict(re.findall(r"--([\w-]+):(#[0-9A-Fa-f]{6});", light.group(1)))
    surfaces = ("#FFFCF6", "#F8F4EC", "#F4EFE6", "#F3F0E9", "#F8F1E8")
    for token in ("text-faint", "cyan", "gold", "teal", "red", "amber", "slate"):
        for surface in surfaces:
            assert _contrast_ratio(tokens[token], surface) >= 4.5

    tinted_badges = {
        "text-faint": tuple(
            ("#071B36", alpha) for alpha in (0.026, 0.034, 0.042, 0.052, 0.06)
        ),
        "slate": (("#64748B", 0.14),),
        "teal": tuple(
            ("#5EEAD4", alpha)
            for alpha in (0.08, 0.10, 0.11, 0.12, 0.13, 0.14)
        ),
        "red": tuple(
            ("#FB7185", alpha)
            for alpha in (0.08, 0.10, 0.11, 0.12, 0.13, 0.14)
        ),
        "gold": (("#D9B54A", 0.12), ("#F6C86B", 0.10)),
        "cyan": tuple(
            ("#E56F2D", alpha)
            for alpha in (0.055, 0.06, 0.075, 0.10, 0.11, 0.12)
        ),
    }
    for token, overlays in tinted_badges.items():
        for surface in surfaces:
            for tint, alpha in overlays:
                background = _blend(tint, alpha, surface)
                assert _contrast_ratio(tokens[token], background) >= 4.5

    assert _contrast_ratio(tokens["on-accent"], tokens["cyan"]) >= 4.5
    assert "color:var(--brand-orange)" not in source
    assert "color:var(--brand-marigold)" not in source


def test_web_shell_publishes_deterministic_social_artwork():
    source = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert 'property="og:image" content="/assets/brand/social-card.png"' in source
    assert 'name="twitter:image" content="/assets/brand/social-card.png"' in source
