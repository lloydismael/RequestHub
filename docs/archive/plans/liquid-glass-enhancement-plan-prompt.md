# Plan Prompt — iOS 26 Liquid Glass Enhancement (Landing / Login Page)

> Archived design plan. For the current layout and commands, see the [README](../../../README.md).

> Paste this into your AI coding agent (Copilot / Claude / Cursor). It forces a
> PLAN first, enforces anti-slop rules, replicates real iOS Liquid Glass motion,
> and mandates full light/dark support. Fill in the **MY STACK / CONTEXT** block
> with your current code before sending.

---

## ROLE
You are a senior front-end engineer + motion designer specializing in Apple's
iOS 26 "Liquid Glass" design language. You obsess over optical fidelity,
60fps motion, and accessible theming.

## OBJECTIVE
Improve and enhance the Liquid Glass look of my landing/login page so it feels
indistinguishable from real iOS 26 Liquid Glass — including its ANIMATION and
interaction physics — with full, first-class light AND dark mode support.

## DELIVER A PLAN FIRST — DO NOT WRITE FINAL CODE YET
Before any implementation, produce a structured plan with these sections:
1. **Audit** — inspect my current markup/CSS and list exactly what reads as fake
   glass today (flat blur, missing saturation, hard edges, no refraction, no
   motion, single-theme). Be specific; cite the offending rules.
2. **Target spec** — the concrete optical + motion properties we will hit.
3. **Implementation phases** — ordered, atomic steps, each independently shippable.
4. **File/component changes** — what gets added or refactored, and why.
5. **Risks & fallbacks** — browser support, performance, accessibility.
6. **Acceptance checklist** — how we verify each requirement is met.

Wait for my approval on the plan before generating code.

## HARD REQUIREMENTS

### A. Optical fidelity (real glass, not "dirty plastic")
- Layered backdrop: `backdrop-filter: blur(Npx) saturate(160–180%)` (+ `-webkit-`
  prefix). **Saturation is mandatory** — a pure blur is banned.
- Semi-transparent **tint** via `rgba()` on the surface (never `opacity`), so a
  physical pane reads without washing out content behind it.
- **Edge refraction / lensing** on curved edges using an SVG `feDisplacementMap`
  (feTurbulence-driven), so the background subtly warps at panel borders — NOT a
  uniform blur rectangle.
- **Motion-reactive specular highlights** via `feSpecularLighting` and/or a
  gradient sheen layer, plus a soft inner top-highlight and inner shadow to fake
  thickness (inset box-shadows).
- **Feathered edges** with `mask-image` — no crisp hard borders.

### B. Animation / interaction physics (must FEEL like iOS)
- **Materialization**: elements appear by modulating blur + scale + opacity
  (glass "condensing" in), not a plain fade.
- **Fluid, gel-like** touch/hover response: spring-based easing (cubic-bezier
  approximating an iOS spring), NOT linear or default ease.
- **Highlight parallax**: specular sheen shifts subtly with pointer position (and
  device orientation via `deviceorientation` where available) to mimic light
  bending as the device moves.
- **Press states** morph fluidly (scale + refraction intensity); buttons/inputs
  feel liquid and instantly responsive.
- Honor `prefers-reduced-motion` — degrade gracefully to static glass.

### C. Light + Dark mode (first-class, not an afterthought)
- Drive everything from CSS custom properties (tint, blur, saturation, sheen,
  shadow, border-glow) themed per mode.
- Respect `prefers-color-scheme` **and** expose a manual toggle (styled as a
  Liquid Glass control itself).
- **Dark mode**: darker tint, cooler highlights, stronger inner glow so glass
  stays legible on dark backgrounds. **Light mode**: brighter sheen, softer shadow.
- Maintain WCAG AA contrast for all text/inputs in BOTH modes.

## ANTI-SLOP CONSTRAINTS (strict)
- No generic glassmorphism boilerplate, no purple 135deg gradient cliché, no
  lorem filler, no unused CSS, no `!important` spam.
- No bloated frameworks/dependencies for effects achievable natively; justify any
  library you introduce.
- Every value must be intentional and commented WHY (not "adjust as needed").
- Reuse tokens/variables — zero magic-number duplication.
- Clean, semantic, accessible HTML; keyboard-navigable login form.
- Production-grade only: DRY, performant, no dead code.

## PERFORMANCE & COMPAT
- Target a smooth 60fps; animate via `transform`/`opacity`; use `contain: strict`
  / `will-change` where it helps compositing.
- Provide graceful fallbacks where `backdrop-filter` or SVG displacement is
  unsupported (Firefox distortion gaps) — never a broken/opaque box.

## MY STACK / CONTEXT
- [Fill in: HTML/CSS/Tailwind/React/etc., and paste current landing/login code]

## OUTPUT FORMAT
Return the PLAN only, in the 6 sections above, as concise scannable bullets.
End by asking me to confirm before you implement.
