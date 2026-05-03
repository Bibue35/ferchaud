# Ferchaud Design System

> **Inspiration:** minara.ai (AI-native, conversation-forward, dark) — but
> our own thing. Think Bloomberg-density meets Linear-quietness.
> Honest about money, calm about it.

This document is the single source of truth for visual decisions. Every
new template should read this file first. If a rule here conflicts with
something in code, the code is wrong, not this doc.

---

## 1. Voice & tone

- **Honest, not hype.** "Realistic returns are 5–25% annual." Not "10x your money in a month."
- **Calm, not loud.** No emoji exclamation marks. No drop-shadows that scream.
- **AI is the brain, not the brand.** We don't say "AI-powered" five times a sentence.
- **Talk like a quant friend.** Short sentences. Numbers that mean something. Never marketing-fluff.

---

## 2. Color palette

We have ONE accent (green). Everything else is grayscale. Two themes — light is default, dark is opt-in.

### Light theme (default)

| Token | Value | Use |
|---|---|---|
| `--bg`             | `#fafaf9` | Page background |
| `--bg-soft`        | `#f4f3f0` | Sections, alternating bands |
| `--surface`        | `#ffffff` | Cards, panels |
| `--surface-2`      | `#f7f6f4` | Sub-cards, inputs |
| `--border`         | `#e8e7e3` | Default border |
| `--border-strong`  | `#cfcdc7` | Hover borders |
| `--text`           | `#0c0d0e` | Primary text |
| `--text-secondary` | `#525355` | Secondary, descriptions |
| `--muted`          | `#8b8d91` | Captions, hints |
| `--accent`         | `#0c0d0e` | Buttons, primary CTAs |
| `--green`          | `#16a34a` | Profit, success, "live" |
| `--red`            | `#dc2626` | Loss, error |
| `--amber`          | `#d97706` | Warning, "be careful" |

### Dark theme (`[data-theme="dark"]`)

| Token | Value | Use |
|---|---|---|
| `--bg`             | `#08090a` | Page background — almost black, never pure |
| `--bg-soft`        | `#0d0e10` | Sections |
| `--surface`        | `#101113` | Cards |
| `--surface-2`      | `#16181b` | Sub-cards |
| `--border`         | `#22252a` | Default border |
| `--border-strong`  | `#373a40` | Hover borders |
| `--text`           | `#f0eee9` | Primary text — slightly warm, not pure white |
| `--text-secondary` | `#9b9ca0` | Secondary |
| `--muted`          | `#65676b` | Captions |
| `--accent`         | `#f0eee9` | Buttons (inverts in dark) |
| `--green`          | `#22c55e` | Profit |
| `--red`            | `#ef4444` | Loss |
| `--amber`          | `#fbbf24` | Warning |

### Banned colors

- No saturated blues / purples / pinks. We are not a Web3 casino.
- No gradients on text. (Single exception: hero glow.)
- No drop shadows with hue. Always `rgba(0, 0, 0, X)`.

---

## 3. Typography

| Role | Font | Weight | Size |
|---|---|---|---|
| Display (hero) | `Inter` | 800 | 56–88px, `letter-spacing: -2px` |
| H1 | `Inter` | 800 | 32–40px, `-1px` |
| H2 | `Inter` | 700 | 22–28px, `-0.5px` |
| Body | `Inter` | 400 | 15–16px, line-height 1.6 |
| Small | `Inter` | 500 | 12–13px |
| Eyebrow | `Inter` | 700 | 11px, uppercase, `letter-spacing: 1.5px`, `--muted` |
| Data / numbers | `'JetBrains Mono', 'SF Mono', Menlo, monospace` | 500 | 13–14px, `font-variant-numeric: tabular-nums` |

Always tabular-nums on every $ figure, % figure, count.

---

## 4. Spacing

Base unit = `4px`. Standard scale: `4, 8, 12, 16, 24, 32, 48, 64, 96`.

- **Page padding:** 24px mobile, 48px desktop.
- **Section vertical:** 96px between major sections, 64px secondary.
- **Card padding:** 24px (mobile) / 32px (desktop).
- **Stack gap inside card:** 16–24px.

---

## 5. Components

### Cards / panels

- Border radius: **18px** for cards, **24px** for hero panels, **50px** (pill) for inline elements.
- Border: `1px solid var(--border)`.
- Shadow: `0 1px 2px rgba(0,0,0,.04), 0 8px 24px rgba(0,0,0,.04)` (light) / `none, 0 8px 24px rgba(0,0,0,.4)` (dark).
- Hover lift: `translateY(-2px)` + slightly stronger shadow. **Never** scale.

### Buttons

- Primary: `var(--accent)` bg, inverted text, **50px** radius, 14px font, 700 weight, 12–14px vertical padding.
- Secondary (outline): transparent bg, `1.5px solid var(--border)`, hover → `border-strong`.
- Ghost: transparent everything except text color, no border.
- Loading state: text becomes "…" + disabled.

### Inputs

- 12px radius (NOT pill — pills get hard to type into long values).
- `--surface-2` bg.
- Focus ring: `0 0 0 3px rgba(0,0,0,.04)` (light) / `rgba(255,255,255,.06)` (dark).
- Label above, 12px font, `--text-secondary`, 600 weight.

### Pills (status chips)

- 50px radius, 2px / 8px padding, 10–11px font, 700 weight.
- LIVE: green tint bg, green text.
- PAUSED: gray tint bg, muted text.
- LEARNING: amber tint bg, amber text.

---

## 6. Layout

- **Max content width:** 1200px (landing) / 1400px (dashboard).
- **Grid:** 12 columns on desktop, 1 on mobile. Use `grid-template-columns: repeat(12, 1fr)` only when alignment matters; default to `flex` or `grid-template-columns: 1fr 1fr` when not.
- **Nav:** floating pill, `position: fixed`, top: 16px, centered, max-width 720px, `backdrop-filter: blur(12px)`.

---

## 7. Motion

- **Standard duration:** 180ms.
- **Easing:** `cubic-bezier(.2, .7, .2, 1)` (subtle ease-out).
- **No spring physics.** No bounces. Trading is not playful.
- **Page transitions:** opacity 0 → 1 over 250ms.
- **Loading shimmers:** 1.5s linear gradient sweep.

Allowed transforms:

```
.hover-lift      { transition: transform 180ms ease-out, box-shadow 180ms ease-out; }
.hover-lift:hover{ transform: translateY(-2px); }
```

Banned:

- `transform: scale(1.05)` on cards (looks like a video game).
- `animation: bounce` (we are not a Slack reaction).
- Parallax (motion sickness, slow on mobile).

---

## 8. Iconography

- 16px / 20px / 24px sizes only.
- `stroke-width: 2`, no fills.
- Lucide icons or hand-tuned inline SVG.
- Color: inherit from parent, never explicit unless semantic (green check, red x).

---

## 9. Honesty rules (UX writing)

- Every $ figure on the dashboard is **the actual figure**, never a fake demo.
- "5–25% annual" is the only return claim we make on landing pages.
- Risk disclosure pops up on first dashboard visit, must be acknowledged.
- Every "AI decision" panel cites its provider (Claude / Grok / Rule-based) so users know what made the call.
- Pricing page never shows "$0/mo" without "paper trading only" beside it.

---

## 10. Page architecture

```
/                  → Landing (hero, how, pricing, footer)
/how-it-works      → Transparency guide (the long-read)
/pricing           → Pricing detail (currently #pricing on landing)
/login             → Auth (login mode)
/signup            → Auth (signup mode)
/register          → 307 → /signup
/logout            → clears cookie, → /
/dashboard         → Main app (no onboarding gate; risk modal on first visit)
/analytics         → Bot brain (learning insights, equity curve, top arms)
/feed              → Live trade + signal feed
/sources           → Data attribution
/legal             → Terms + privacy
/health, /readyz, /livez, /metrics → cloud supervisor endpoints
```

No `/onboarding`. New users land in `/dashboard` directly. Connections (broker, funding) are progressive — always reachable from a "Connections" link in the dashboard, never blocking.

---

## 11. Component checklist for every new page

- [ ] Uses `base.html`
- [ ] No emojis (unless user explicitly requested)
- [ ] All colors via CSS variables — no inline hex
- [ ] Tabular-nums on every numeric figure
- [ ] `font-variant-numeric: tabular-nums` on `$`, `%`, count, time
- [ ] Mobile-tested at 375px wide
- [ ] Dark mode tested
- [ ] Skeleton states for async data (not just blank)
- [ ] Empty states ("No trades yet — start the bot") for empty data
- [ ] Error states (toast + inline alert)

---

## 12. What "perfect" looks like

When in doubt, ask:

1. Does this look like a 2026 fintech tool from a serious team? Or a 2017 ICO landing page?
2. If I added one more drop shadow, would it look better? (Probably not.)
3. Could a 70-year-old see this? (Font ≥ 14px on body, contrast ratio > 4.5.)
4. Does the page tell the truth about money? (No "guaranteed 1% daily" type claims.)
5. Would a Bloomberg user say "this respects my time"?
