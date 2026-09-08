# Settings UI contract

Use the existing IBM Plex / SCSS design system and shared components. Settings
must pass the same checks as the rest of the panel.

- Wrap each form section in `settings-card` or use `settings-form` on the form.
  A form inside a card inherits that card rather than drawing a second surface.
- Use `FormField` with `Input`, `Select`, and `Switch`. Native text fields inside
  `FormField` receive shared styling too; checkboxes and radios keep their size.
- Put the label on the left and the switch on the right for a boolean preference.
  Use checkboxes for row selection and acknowledgements. Do not put colored pills
  around a control or style every descendant `span` (Radix uses internal spans).
- Keep text readable on tinted backgrounds. Use the primary text token for
  settings alerts and selected navigation; reserve accent color for controls.
- For section-specific create actions, use the right side of the section header
  or the existing view picker's action slot. Page actions still use the topbar.
  Keep save actions in the form footer with the shared spacing classes.
  Use `settings-actions settings-actions--footer` for paired footer actions:
  secondary first, primary rightmost, 12px gap, stacked on small screens.
- Use `EmptyState`. Inside a card it inherits the surface and drops its border.
  Use `stat-strip` for a group of metrics, with a distinct muted cell surface.
- Sidebar, topbar, and statusbar all use `--bg-sidebar`. The statusbar has a
  divider, without an extra bright surface or shadow.
- Name scope explicitly. Personal notification destinations differ from shared
  server delivery settings. Slack currently has no per-user destination.
- Never reset an unrelated draft after saving another section. A switch that
  saves immediately must say so, keep its previous state if the save fails, and
  refresh consumers after success.

## Verification

Run `npm run lint`, `npm run build`, and `npm run test:settings-ui` in `frontend`.
The browser regression renders actual components and the complete SCSS cascade,
with synthetic API responses. It checks light/dark surfaces, mobile overflow,
model discovery and exact IDs, secret preservation, assistant enable persistence
and failures, notification scope, and section action placement. CI runs it and
uploads screenshots from `frontend/test-results/`.

Provider discovery tests do not contact external providers. Live connectivity
must be verified separately with a configured connection. Model capabilities and
pricing should only be shown when supplied by a trustworthy provider catalog;
do not infer them from a model name.
