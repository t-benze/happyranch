# Documentation map

Use the source that matches the question:

- [Project README](../README.md) — end-user setup and product surface.
- [Agent and developer guides](agent-guides/) — maintained architecture,
  configuration, executor, orchestration, feature, web, and CLI guidance.
- [Operations](operations/) — operator runbooks and release checklists.
- [Product documents](product/) and [PRDs](product/prds/) — product intent,
  conventions, and scoped requirements; each document's status and the shipped
  implementation still matter.
- [Manual site](manual/pages/index.md) — source for the user-facing manual.
- [Architecture decisions](adr/) — durable decisions with their recorded scope.
- [Superpowers specs](superpowers/specs/README.md) and
  [plans](superpowers/plans/README.md) — indexed design history and historical
  implementation plans.

For current behavior, prefer implementation, behavior tests, the
[OpenAPI snapshot](../tests/contract/openapi.json), and the maintained guides
above. [CLAUDE.md](../CLAUDE.md) provides the repository-wide reading map.
Historical plans and specs do not override those sources unless the specs
index explicitly marks a spec current. Dated product and `design-overhaul/`
documents likewise record intent or project context; do not assume every such
document is a current behavior contract.
