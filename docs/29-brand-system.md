# IntelliDhan Brand System

**Status:** Implemented foundation

**Direction:** D4.2 — calm owl-and-lotus mark, soft-flared editorial wordmark,
orange bindu

**Last updated:** 2026-07-21

## 1. Identity

The IntelliDhan identity combines a watchful owl and a three-petal lotus inside
a calm midnight oval. The owl represents attentive research and measured
judgment; the lotus quietly references Indian visual culture and clarity without
turning the product into a religious motif. A burnt-orange bindu anchors the
mark as its single focal point.

The wordmark uses a soft flared editorial serif. It gives the product warmth and
authority without resembling a legacy bank. `Intelli` is midnight navy (or
parchment on dark surfaces); `Dhan` is marigold.

The production SVGs are the approved reference. The earlier
`web/assets/brand/intellidhan-brand-board.png` remains an archived exploration
board only; production UI must not extract pixels or geometry from it.

## 2. Production asset stack

| Asset | Purpose |
|---|---|
| `intellidhan-mark.svg` | Primary owl-and-lotus mark on light/neutral surfaces |
| `intellidhan-mark-inverse.svg` | Keyed owl-and-lotus mark on midnight/navy surfaces |
| `intellidhan-lockup.svg` | Horizontal presentation and external collateral |
| `favicon.svg` | Responsive micro-mark that fills a midnight rounded tile |
| `social-card.svg` | Editable 1200×630 social/share card with outlined text |
| `social-card.png` | Deterministic 1200×630 Open Graph/Twitter delivery asset |
| `intellidhan-theme.css` | Platform palette, typography, and component layer |
| `intellidhan-brand-board.png` | Archived first-generation exploration board only |

SVG is the source of truth. Raster exports should be generated from these files
at delivery size so edges remain sharp. Do not trace the reference PNG back into
production artwork. The lockup and share-card lettering is converted to vector
outlines, so exported geometry does not depend on installed fonts. Accessible
names remain in each SVG's `title` and `desc` elements.

## 3. Palette

| Name | Hex | Primary role |
|---|---:|---|
| Midnight | `#071B36` | Logo structure, dark canvas |
| Navy | `#0B203D` | Navigation and primary panels |
| Ink | `#102B4E` | Elevated cards |
| Marigold | `#F6A800` | `Dhan`, evidence accents |
| Burnt orange | `#E56F2D` | Bindu, selection, primary action |
| Parchment | `#F8F4EC` | High-emphasis text/light canvas |
| Mist | `#AEBACB` | Secondary text |

Use an approximate 70/20/8/2 distribution for navy, neutrals, marigold, and
orange. Orange is deliberately scarce; it should guide the eye, not tint the
entire terminal.

Market semantics remain distinct and labeled: gain `#4AC39B`, loss `#EF6A78`,
warning `#E3A83B`, and chart-context blue `#7BA7D7`. These colors must never
replace the orange bindu in the identity.

## 4. Typography

- **Newsreader** 500–700: logo and high-level editorial headings.
- **Manrope** 400–800: interface, controls, cards, and prose.
- **JetBrains Mono** 500–700: tickers, prices, levels, timestamps, and metrics.

The wordmark should not be recreated with a generic sans. Conversely, dense
trading data should never use the display serif. The live app currently loads
these families from Google Fonts with system fallbacks and `display=swap`;
external brand SVGs do not depend on that request. Self-host subsetted WOFF2
files in a later deployment-hardening pass if privacy policy or CSP requires it.

## 5. Logo usage

- Clearspace around the mark: at least the diameter of the orange bindu.
- Minimum mark size: 24px in digital UI; use 32px or larger when possible.
- At 16px use the enlarged favicon micro-mark; do not shrink the standard mark
  into additional padding.
- Minimum lockup width: 180px.
- Never stretch, rotate, bevel, shadow, or recolor the owl or individual petals.
- Keep both eyes balanced and preserve the oval enclosure; do not detach the owl
  from the lotus or use either motif as a standalone product mark.
- Never turn the bindu green/teal or add a glow around it.
- On dark backgrounds use the inverse mark; on light backgrounds use the navy
  mark. Marigold and orange remain unchanged in both.

## 6. Product application

The terminal is navy-led and card-centric. Orange identifies selected state,
primary calls to action, keyboard focus, and brand moments. It is not a generic
status color. Bullish/bearish/warning states retain their semantic colors plus
text labels so users never infer meaning from color alone.

Surfaces use at most a two-stop navy gradient, 18–20px radii, quiet borders, and
low-spread shadows. The serif is limited to the welcome, daily brief, and major
analysis headings. These limits preserve the warmth of the logo without making
the analytics experience ornate or gaudy.

The agent-readable implementation contract is maintained in
`design-system/intellidhan/MASTER.md`, with terminal-specific rules in
`design-system/intellidhan/pages/terminal.md`.

## 7. Verification record

The 2026-07-21 D4.2 pass supersedes the first-generation `ID` monogram. It
verified shared owl, lotus, enclosure, eye, and bindu geometry across the mark,
inverse mark, favicon, lockup, and social card; small-size legibility at 16, 24,
34, and 256px; deterministic social export with an approved content hash;
gateway delivery; and the focused and full regression suites. Strategy, data,
authentication, multi-brain, and broker execution behavior did not change.

The 2026-07-19 implementation pass verified:

- XML parsing and raster render inspection for the mark, inverse mark, lockup,
  favicon, and share card at 16, 24, 34, 256, and presentation sizes;
- a shared closed lotus base with three readable petal peaks and no marigold
  notch or endpoint cusp across all five SVG variants;
- deterministic outlined lettering in external artwork and an exact 1200×630
  PNG share export wired into Open Graph and Twitter metadata;
- WCAG AA small-text contrast for light-mode faint, action, evidence, disabled,
  warning, bullish, and bearish tokens across flat and tinted panel, elevated,
  card-gradient, badge, and page surfaces;
- correct `/assets` SVG/CSS/PNG MIME types, ETag revalidation, and traversal
  rejection from the FastAPI gateway;
- dark and light browser themes with no console errors;
- no horizontal document overflow at 375, 768, 1024, or 1440px;
- focused brand/UI tests and the full non-integration regression suite
  (`293 passed, 6 deselected`);
- Ruff checks for the touched Python files.

The Adobe connector required reauthentication during this pass. The checked-in
SVG files are editable, Adobe/Illustrator-ready vector sources; no production
behavior depends on Adobe availability.
