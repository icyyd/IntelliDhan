# IntelliDhan Design System

This is the machine-readable visual source of truth. Page files under `pages/`
may narrow these rules but must not redefine the logo, palette, typography, or
semantic-market colors.

## Brand direction

- Personality: modern heritage fintech; confident, calm, warm, and analytical.
- Identity: D4.2 owl-and-lotus mark with the 4A soft-flared editorial wordmark.
- Symbolism: the owl is attentive research and measured judgment; the lotus is
  implicit resilience and clarity; a burnt-orange bindu anchors the mark. Never
  recolor that dot green or teal.
- Visual restraint: navy is structural, parchment carries readable content,
  marigold identifies the name, and orange marks a primary action or selection.

## Color tokens

| Token | Hex | Use |
|---|---:|---|
| `brand-midnight` | `#071B36` | Primary dark canvas, logo structure |
| `brand-navy` | `#0B203D` | Navigation and panels |
| `brand-ink` | `#102B4E` | Elevated surfaces |
| `brand-marigold` | `#F6A800` | `Dhan`, evidence and warm highlights |
| `brand-orange` | `#E56F2D` | Signal dot, primary action, active selection |
| `brand-parchment` | `#F8F4EC` | High-emphasis text and light surfaces |
| `brand-mist` | `#AEBACB` | Secondary text |

Recommended visual ratio: 70% navy, 20% neutrals, 8% marigold, 2% orange.
Orange must not fill large panels or compete with market-status colors.

Semantic colors are data, not brand decoration:

- gain/ready: `#4AC39B`;
- loss/block: `#EF6A78`;
- warning/stale: `#E3A83B`;
- chart context/EMA: muted blue `#7BA7D7`.

Never encode trade state with color alone; pair color with text and/or an icon.

## Typography

- Display and brand: **Newsreader**, 500–700. Use for the wordmark, welcome
  headline, daily-brief headline, and major analysis titles only.
- Interface: **Manrope**, 400–800. Use for controls, labels, cards, and prose.
- Market data: **JetBrains Mono**, 500–700. Use for tickers, prices, levels,
  timestamps, and performance metrics.
- Maintain a system fallback stack and do not render paragraphs in the serif.
- External brand SVGs use outlined glyphs so the lockup and social artwork are
  deterministic when Newsreader or Manrope is unavailable. The live terminal
  keeps system fallbacks while the current Google Fonts dependency is active.

## Surfaces and geometry

- Card radius: 18–20px desktop, 16–18px compact/mobile.
- Control radius: 11–14px; pills are reserved for statuses and compact filters.
- Use opaque or near-opaque navy surfaces behind trading decisions.
- Shadows are low-spread and cool; decorative glow opacity stays below 8%.
- No neon edges, multi-color auroras, oversized glass blur, or 3D effects.
- Hover feedback changes border/color; never scale trading cards.

## Information hierarchy

1. Today: welcome/daily brief, SPX-SPY-QQQ context, top-three focus.
2. Action: rich signal cards with entry, invalidation, targets, risk, and age.
3. Evidence: selected signal detail and optional charts.
4. Secondary: wider radar, automation status, and historical evidence.

The user should be able to identify the actionable ticker, setup state, entry
zone, invalidation, and risk without opening a chart.

## Accessibility and responsive rules

- Minimum body contrast is WCAG AA (4.5:1); large display text is 3:1.
- Use a visible 2px theme action-orange focus ring with 2px offset.
- Interactive targets are at least 44px where space permits.
- Respect `prefers-reduced-motion` and the product reduced-motion preference.
- Verify at 375px, 768px, 1024px, and 1440px with no horizontal page scroll.
- Keep light mode functional using parchment surfaces and dark burnt-orange
  controls; never place standard orange body text on white.

## Asset map

Production assets live in `web/assets/brand/`:

- `intellidhan-mark.svg` — light-surface owl-and-lotus mark;
- `intellidhan-mark-inverse.svg` — dark-surface keyed owl-and-lotus mark;
- `intellidhan-lockup.svg` — horizontal brand lockup;
- `favicon.svg` — app/browser tile;
- `social-card.svg` — editable 1200×630 share-card source with outlined text;
- `social-card.png` — deterministic 1200×630 delivery export for previews;
- `intellidhan-theme.css` — web implementation tokens and overrides;
- `intellidhan-brand-board.png` — archived first-generation exploration board,
  not a production source asset.

## Forbidden patterns

- green or teal signal/bindu dot;
- detached owl, lotus, or bindu used as a replacement product mark;
- orange used as bullish and red as bearish without labels;
- gradients that span more than two close navy tones;
- decorative emojis as core icons;
- serif type for dense numbers, tables, forms, or long prose;
- layout-shifting hover effects;
- charts above actionable signal plans on the Today screen.
