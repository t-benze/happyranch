# Reviewed sequential examples (non-executable data)

These are the unchanged TASK-14397 reviewed-baseline examples carried in
TASK-14461 revision 3, approved by Founder THR-336 message 85. These original
file bytes include a presentation newline; canonical definition bytes exclude
that final byte. No example is an executable request or an
admission/permission grant. Documentation fixtures are not catalog entries.

| File | Bytes | SHA256 |
| --- | --- | --- |
| `runtime/workflows/builtin_templates/coding.v2.json` (bundled data, no registered selector) | 3795 | `5039e9eb60a03f8de110b40123a123d25962fbff48c39af5271a0666d679d065` |
| [draft-review-v1.json](draft-review-v1.json) | 2055 | `9bd833e0ea0f474ef1bcdc5d291603d508320747a4699e9cdb3286e8b5c97800` |
| [draft-review-v2-data-variation.json](draft-review-v2-data-variation.json) | 2723 | `f3906d2a17efdda13bf7776e74a9b9c26c0b8d1e6e57913edc99ba74985d3713` |
| [merge-guard-without-review-qa-v1.json](merge-guard-without-review-qa-v1.json) | 2579 | `55cfc9f4ffd00f0962a1a6b19560bafa2372fb570285b105517fcd216f108628` |

Canonical definition SHA256 values, in the same row order, are
`a3b012d117c5905a1bac3c67a3158a8b1afa5a571acdba73c876ac7de24f790c`,
`859b0b2eeb4ea58bd82c54b11f7a194feeb431029cf0d182125c8c91a8fe85ec`,
`81178c8b6ac1292c6222d80cd718d2119fdef2e864d1b97a3abf9dd9c842da89`,
and `96b5639c1b5ef2bb3b90ed43693a6904cac736cf1eca31d3e27d995ea44af5bc`.

Coding and Draft/Review share the new definition grammar. The Draft/Review
variation changes author responsibilities and adds `second_read` without a
new check or interpreter branch. The omitted-producer fixture changes Coding's
graph to develop→finalize, with four roles and only a current change input;
its trusted merge gate must independently require review and QA.

The accepted requirement-to-case map remains TASK-14397 A01–A21 and
TASK-14461 acceptance-plan revision 3. Relevant U1 interfaces and future
observations are:

| Requirement/case | Input/scenario | Expected observable result | Level/venue and status |
| --- | --- | --- | --- |
| F1/AC1, A01/A02 | All four exact examples; variation changes responsibilities/sequence | Same validator accepts bounded graphs; common U1b interpreter renders changed text/routes | V1 source inspection; behavioral V3 NOT RUN |
| F1/F6/AC1/AC8, A02 | Missing role/successor, unreachable node, unknown field/check/outcome, cycle without consumed counter, required input bypasses producer, terminal missing witness | Categorical refusal before any future allocation | V3 NOT RUN; no new harness |
| F3/F6/AC5/AC8 | Duplicate/noncanonical JSON, Boolean integers, unknown ABI, malformed UTF8, bounds; incomplete/nonindependent bindings | Closed syntax/pin refusal; original lower limits retained | V3 NOT RUN |
| F1/F3, A12 | v1 review return; v2 return specifically from second_read | U1b creates fresh author epoch; original inputs/findings retained, both review selections retired, new changed document reviewed | U1b plus actual V3 service NOT RUN |
| F3/F6/AC8 | Lower original limit/exhausted epoch/visit/event allowance | U1b holds, no extra successor, no counter reset | U1b/V3 NOT RUN |
| F3/F5, A13 | Valid develop→finalize graph with missing mandatory evidence | Actual trusted merge selection refuses before executable selection/intent/action; admissible comparison reaches same guard | U5/V3 NOT RUN; graph validation is not guard proof |

U1a adds no test assertions or test fixtures consumed by retained tests. This
table records expected contracts and missing execution, never behavioral PASS.
