<!-- Describe the concrete problem and resulting behavior. Keep detailed evidence
     once in the existing case record; link it here. Remove inapplicable entries
     with a reason. Preserve required engineering evidence and historical failures. -->

## Change

## Verification

- Base/head, Native Impact Evidence, final diff/radius and maintained doc parity:
- Commands, actual exits, source/runtime receipts and affected case record:
- Required clean-head `scripts/local_ci.sh all` receipt and exact-head hosted CI:
- General integration disposition: SKIPPED under Founder THR-243 seq42 (never PASS).
- For changed tests: four authoring answers, named test/producer mapping,
  attributable RED, byte-exact restoration/GREEN and keeper disposition:

## Web bilingual review (when affected)

- [ ] New/changed app-owned headings, actions, dialogs, tooltips, accessible
  labels, validation, loading/empty/error states and generated narratives ship
  en and zh-CN in this PR through typed catalogs and localized shared-UI props.
- [ ] Named parameters, plurals and locale-aware helpers are used; authored
  content, machine identifiers and raw diagnostics remain verbatim.
- [ ] Both locales cover affected populated/loading/empty/error states at
  390×844 and 1440×900. Evidence checks actual text/control bounds against
  clipping ancestors, readability and pointer/keyboard reachability.
- [ ] Both switch directions preserve the same mounted nodes, authored drafts,
  focus and selection, with zero locale-triggered HTTP requests or mutations
  and zero transport restarts/messages. Original actions and navigation work.
- [ ] Human review checked meaning and Chinese layout. Untranslated owned copy
  or broken Chinese layout requires REQUEST_CHANGES; structural automation
  does not establish translation quality or usability.
