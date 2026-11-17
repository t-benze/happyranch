# Documentation map

Use the source that matches the question:

- [Project README](../README.md) — end-user setup and product surface.
- [Agent and developer guides](agent-guides/) — maintained architecture,
  configuration, executor, orchestration, feature, web, and CLI guidance.
- [Product E2E reporting and browser observations](../e2e/README.md#current-reporting-contract--slice-3-of-3-thr-291-seq659) — current lane/Health and standard diagnostic contract.
- [Operations](operations/) — operator runbooks and release checklists.
- [Product documents](product/) and [PRDs](product/prds/) — product intent,
  conventions, and scoped requirements; each document's status and the shipped
  implementation still matter.
- [Manual site](manual/pages/index.md) — source for the user-facing manual.
- [Architecture decisions](adr/) — durable decisions with their recorded scope.
- [Superpowers specs](superpowers/specs/README.md) — retained current and
  referenced designs. Historical implementation plans were removed; see Git history.

For current behavior, prefer implementation, behavior tests, the
[OpenAPI snapshot](../tests/contract/openapi.json), and the maintained guides
above. [CLAUDE.md](../CLAUDE.md) provides the repository-wide reading map.
Retained specs do not override those sources unless the specs index explicitly
marks a spec current. Dated product documents record intent or project context;
do not assume every such document is a current behavior contract. Removed
historical design documents remain available in Git history.
