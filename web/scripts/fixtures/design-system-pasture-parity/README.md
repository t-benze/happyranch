Fixture for `scripts/test-design-system-pasture-parity.sh` (THR-376 PR12).

A trimmed Pasture reference (`design/pasture-ds.css`), a matching `tokens.css`,
a Tailwind default-theme subset and minimal stand-ins for the five owning
components. The canonical fixture is in parity (exit 0); the suite mutates one
token, class or reference value at a time and expects exit 7 with the exact
drift line. The `@media` block and Direction B rules are deliberately present
so the suite can prove they are ignored.
