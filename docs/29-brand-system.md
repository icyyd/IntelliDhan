# IntelliDhan Brand System

**Status:** Implemented foundation

**Direction:** 4A — soft-flared editorial wordmark, orange signal dot

**Last updated:** 2026-07-19

## 1. Identity

The IntelliDhan identity combines an `ID` monogram, a three-petal lotus, and a
rising signal curve. The lotus quietly references Indian visual culture without
turning the product into a religious motif. The curve connects analysis to
action, while the burnt-orange bindu is the single focal point.

The wordmark uses a soft flared editorial serif. It gives the product warmth and
authority without resembling a legacy bank. `Intelli` is midnight navy (or
parchment on dark surfaces); `Dhan` is marigold.

The approved reference is `web/assets/brand/intellidhan-brand-board.png`.
Production UI must use the SVG assets rather than extracting pixels from that
board.

## 2. Production asset stack

| Asset | Purpose |
|---|---|
| `intellidhan-mark.svg` | Primary monogram on light/neutral surfaces |
| `intellidhan-mark-inverse.svg` | Monogram on midnight/navy surfaces |
| `intellidhan-lockup.svg` | Horizontal presentation and external collateral |
| `favicon.svg` | Browser/app icon on a midnight rounded tile |
| `social-card.svg` | 1200×630 social/share card |
| `intellidhan-theme.css` | Platform palette, typography, and component layer |
| `intellidhan-brand-board.png` | Approved visual reference only |

SVG is the source of truth. Raster exports should be generated from these files
at delivery size so edges remain sharp. Do not trace the reference PNG back into
production artwork.

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
trading data should never use the display serif.

## 5. Logo usage

- Clearspace around the mark: at least the diameter of the orange bindu.
- Minimum monogram size: 24px in digital UI; use 32px or larger when possible.
- Minimum lockup width: 180px.
- Never stretch, rotate, outline, bevel, shadow, or recolor individual petals.
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
