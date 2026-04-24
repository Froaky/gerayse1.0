---
name: ui-desktop-first
description: keep desktop layouts clean, spacious, and intuitive for operational Django screens while preserving mobile support.
---

Use this skill when editing templates or CSS for operator/admin screens where desktop is the primary environment.

Principles:
1. Desktop-first readability: prioritize clear hierarchy, spacing, and scanability at >= 1024px.
2. Avoid accidental narrow content: public/auth pages must not inherit two-column app-shell constraints.
3. Navigation clarity: separate primary navigation from user/session actions.
4. Stable grids: use responsive `auto-fit/minmax` cards to avoid giant empty zones.
5. Form comfort: inputs and actions should be easy to scan and complete with keyboard/mouse.
6. Mobile compatibility: preserve valid behavior at smaller breakpoints without breaking desktop.

Guardrails:
- If a page appears "compressed" on desktop, review global `.layout` media queries first.
- For non-authenticated pages, force single-column shell via `.layout--auth`.
- Keep visual density moderate: no overpacked toolbars or oversized whitespace gaps.
- Add concise CSS comments near breakpoints that affect global layout behavior.

Output checklist:
- Main content uses available desktop width.
- Header/navigation aligns without wrapping chaos.
- Cards/forms have consistent spacing and alignment.
- No horizontal overflow at common laptop widths (1366x768, 1440x900).
- Login and marketing/public pages remain visually balanced.
