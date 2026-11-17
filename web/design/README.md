# web/design — Pasture design reference

`pasture-ds.css` is the founder-approved Pasture design-system stylesheet
(`ds.css`). Its Direction A (`[data-dir="a"]`) light and dark blocks are the
visual authority for the web app's colour values, including the WCAG AA
contrast values in `src/design-system/tokens/tokens.css`.

- **Provenance:** THR-376 seq9 (founder approval). The file is a byte-identical
  copy of org artifact `THR-376-20261117T073852Z-ds.css`
  (23470 bytes, SHA-256
  `2dc3d6ce175dcbc62ba900f28c52cd522571d58235fa3c9e506e8b4a4d12f877`).
  Do not edit it by hand; replace it only with a newly approved artifact.
- **Reference only:** the app build does not import it. Product tokens live in
  `src/design-system/tokens/tokens.css`. The Direction B blocks and other theme
  variants in this file are not the product direction.
- **Scanners:** it sits outside `src/`, so the design-system colour and token
  scanners (`scripts/verify-design-system.sh`) and ESLint (`src/**/*.{ts,tsx}`)
  do not read it. No exclusion rule was needed.

See `../DESIGN_SYSTEM.md` ("Colour contrast") for how the tokens map to it.
