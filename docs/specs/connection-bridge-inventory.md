# Connection bridge command/event inventory

Status as of origin/main 96f64df29653d060711fc4b64fa2d40a88b0ab47, 2026-11-19

This is the maintained source inventory for the [web/native bridge spec](2026-11-17-web-native-bridge.md), not native or cross-device acceptance. **Linux: 16/19 commands implemented; Mac: 2/19 commands implemented.** Events are counted separately: Linux 2/2, Mac 0/2. A parsed/allowlisted name or an unavailable reply does not count as an implementation.

## Facade and closed v1 list

The page-visible facade is defined in [`web/connection/src/bridge.ts:4`](../../web/connection/src/bridge.ts#L4), with the contract in [spec §7](2026-11-17-web-native-bridge.md#js-facing-api-founder-thr-379-seq2):

```ts
window.happyranchConnection.call(command: string, data: string): Promise<string>
window.happyranchConnection.on(eventName: string, handler: (data: string) => void): () => void
```

`data` and resolved results are JSON strings; failures reject with a closed JSON error string. `on` delivers event JSON strings and returns unsubscribe. The facade exists only in the admitted packaged control document.

The tables follow the **19 keys of `commands` and 2 keys of `events`** in [`protocol/native-connection-bridge.v1.json`](../../protocol/native-connection-bridge.v1.json); the request command enum agrees. Reproduce the closed lists from the repository root without executing a native action:

```sh
python -c 'import json; s=json.load(open("protocol/native-connection-bridge.v1.json")); print("commands:", len(s["commands"]), *s["commands"], sep="\n"); print("events:", len(s["events"]), *s["events"], sep="\n")'
```

## Source status

Source abbreviations in status cells link to the exact file/line: **LC** = Linux `connection_contract.py`, **LW** = Linux `connection_window.py`, **MW** = Mac `ConnectionWindow.swift`, **MS** = Mac `ConnectionScripts.swift`. Mac's [closed parser](../../app/mac/Sources/HappyRanchApp/ConnectionContract.swift#L89) accepts all 19 names, but its [dispatcher](../../app/mac/Sources/HappyRanchApp/ConnectionWindow.swift#L232) backs only hello/status. Deferred Mac host status follows [founder option B, spec §5](2026-11-17-web-native-bridge.md#deferred-by-founder-thr-379-seq23).

**Pending Mac work:** [#1668](https://github.com/t-benze/happyranch/pull/1668) is open at `e49359ce62a0c2f326d3a2b34a07a637c0e370f4`. Its [backed list](https://github.com/t-benze/happyranch/blob/e49359ce62a0c2f326d3a2b34a07a637c0e370f4/app/mac/Sources/HappyRanchApp/ConnectionContract.swift#L397) has status, savedComputers, health and ordinary retrySave, plus client events. It explicitly [refuses consent-dependent actions](https://github.com/t-benze/happyranch/blob/e49359ce62a0c2f326d3a2b34a07a637c0e370f4/app/mac/Sources/HappyRanchApp/ConnectionWindow.swift#L312); accept/connect/remove/disconnect/cancel/retry stay planned for 3b-2b under TASK-17661. Open-PR work is excluded from totals.

All web files below are relative to [`web/connection/`](../../web/connection/): one `index.html` entry at `happyranch-shell://connection/index.html`, with role views rather than daemon routes. Controls are capability gated. “Yes — planned” describes the required future ceremony, not a prompt available on main.

CLI entries are Linux-only at this snapshot; **Mac has no working equivalent** ([platform dispatch](../../cli/commands/connection.py#L41), TASK-17661 owns its CLI). Registered refusals are marked explicitly and do not imply parity. `happyranch connection status` reports platform/role availability, not bridge.hello negotiation.

### Commands

| Command | Role | Read vs mutation | Native dialog/system prompt involved | Linux native status | Mac native status | CLI equivalent | Web screen (file/entry) | PR(s) that landed it |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `bridge.hello` | bridge | read | No | implemented ([LW:445](../../packaging/linux/desktop/connection_window.py#L445)) | implemented ([MW:235](../../app/mac/Sources/HappyRanchApp/ConnectionWindow.swift#L235)) | none | `src/bridge.ts:26` (shell admission) | Linux #1648; Mac #1654; shell #1647 |
| `host.status` | host | read | No | implemented ([LW:448](../../packaging/linux/desktop/connection_window.py#L448)) | deferred (founder option B; [MW:237](../../app/mac/Sources/HappyRanchApp/ConnectionWindow.swift#L237), spec §5) | `happyranch connection host status` | `src/host.ts:112` (Host / Check again) | Linux #1648, #1696; web #1694; CLI #1698 |
| `host.enable` | host | mutation | Yes — planned native Enable / fresh system authorization | planned (THR-327 / TASK-17385, slice 2b); unavailable ([LC:305](../../packaging/linux/desktop/connection_contract.py#L305), [LW:483](../../packaging/linux/desktop/connection_window.py#L483)) | deferred (founder option B; [MW:237](../../app/mac/Sources/HappyRanchApp/ConnectionWindow.swift#L237), spec §5) | none (registered refusal) | `src/host.ts:381` (Host / Turn on; capability gated) | schema #1647; web #1694; no native landing |
| `host.turnOff` | host | mutation | Yes — planned native destructive confirmation / system authorization | planned (THR-327 / TASK-17385, slice 2b); unavailable ([LC:305](../../packaging/linux/desktop/connection_contract.py#L305), [LW:483](../../packaging/linux/desktop/connection_window.py#L483)) | deferred (founder option B; [MW:237](../../app/mac/Sources/HappyRanchApp/ConnectionWindow.swift#L237), spec §5) | none (registered refusal) | `src/host.ts:397` (Host / Turn off; capability gated) | schema #1647; web #1694; no native landing |
| `host.createInvite` | host | mutation | Yes — planned system authorization + native invite display/copy | planned (THR-327 / TASK-17385, slice 2b); unavailable ([LC:305](../../packaging/linux/desktop/connection_contract.py#L305), [LW:483](../../packaging/linux/desktop/connection_window.py#L483)) | deferred (founder option B; [MW:237](../../app/mac/Sources/HappyRanchApp/ConnectionWindow.swift#L237), spec §5) | none (registered refusal) | `src/host.ts:388` (Host / Add device; capability gated) | schema #1647; web #1694; no native landing |
| `host.approveDevice` | host | mutation | Yes — Linux fresh polkit actual-device prompt | implemented ([LW:448](../../packaging/linux/desktop/connection_window.py#L448)) | deferred (founder option B; [MW:237](../../app/mac/Sources/HappyRanchApp/ConnectionWindow.swift#L237), spec §5) | `happyranch connection host approve-device` | `src/host.ts:383` (Host / pending approval) | Linux #1696; web #1694; CLI #1698 |
| `host.rejectDevice` | host | mutation | Yes — Linux fresh polkit actual-device prompt | implemented ([LW:448](../../packaging/linux/desktop/connection_window.py#L448)) | deferred (founder option B; [MW:237](../../app/mac/Sources/HappyRanchApp/ConnectionWindow.swift#L237), spec §5) | `happyranch connection host reject-device` | `src/host.ts:384` (Host / pending approval) | Linux #1696; web #1694; CLI #1698 |
| `host.nextDevice` | host | mutation (native selection cursor only) | No — no authorization/effect on device access | implemented ([LW:448](../../packaging/linux/desktop/connection_window.py#L448)) | deferred (founder option B; [MW:237](../../app/mac/Sources/HappyRanchApp/ConnectionWindow.swift#L237), spec §5) | `happyranch connection host next-device` | `src/host.ts:345` (Host / Select next device) | Linux #1696; web #1694; CLI #1698 |
| `host.revokeDevice` | host | mutation | Yes — Linux fresh polkit captured-device prompt; web also confirms | implemented ([LW:448](../../packaging/linux/desktop/connection_window.py#L448)) | deferred (founder option B; [MW:237](../../app/mac/Sources/HappyRanchApp/ConnectionWindow.swift#L237), spec §5) | `happyranch connection host revoke-device` | `src/host.ts:346` (Host / Revoke selected device) | Linux #1696; web #1694; CLI #1698 |
| `client.status` | client | read | No | implemented ([LW:452](../../packaging/linux/desktop/connection_window.py#L452)) | implemented, coarse in-memory projection ([MW:242](../../app/mac/Sources/HappyRanchApp/ConnectionWindow.swift#L242)) | `happyranch connection client status` (invocation-only; notOwned) | `src/main.ts:87`, `src/client.ts:165` (Client / Check again) | Linux #1675; Mac #1654; web #1691; CLI #1705 |
| `client.acceptInvite` | client | mutation | Yes — Linux native paste + target/save consent; Mac sheets planned | implemented ([LW:467](../../packaging/linux/desktop/connection_window.py#L467)) | planned (3b-2b, TASK-17661); unavailable on main ([MW:244](../../app/mac/Sources/HappyRanchApp/ConnectionWindow.swift#L244)) | `happyranch connection client accept-invite` (foreground) | `src/client.ts:470` (Client / Accept invite) | Linux #1675, #1689; web #1691; CLI #1705; no Mac landing |
| `client.savedComputers` | client | read | No — Linux does not request keyring unlock; #1668 projects existing Mac discovery | implemented ([LW:456](../../packaging/linux/desktop/connection_window.py#L456)) | in open PR #1668; unavailable on main ([MW:244](../../app/mac/Sources/HappyRanchApp/ConnectionWindow.swift#L244); [#1668 source:306](https://github.com/t-benze/happyranch/blob/e49359ce62a0c2f326d3a2b34a07a637c0e370f4/app/mac/Sources/HappyRanchApp/ConnectionWindow.swift#L306)) | `happyranch connection client saved-computers` | `src/main.ts:93`, `src/client.ts:159` (Client / saved list) | Linux #1689; web #1691; CLI #1705; Mac pending #1668 |
| `client.connectSaved` | client | mutation | Yes — Linux exact saved-target confirmation; Mac sheet planned | implemented ([LW:458](../../packaging/linux/desktop/connection_window.py#L458)) | planned (3b-2b, TASK-17661); unavailable on main ([MW:244](../../app/mac/Sources/HappyRanchApp/ConnectionWindow.swift#L244)) | `happyranch connection client connect-saved` (foreground) | `src/client.ts:296` (Client / saved computer) | Linux #1689; web #1691; CLI #1705; no Mac landing |
| `client.removeSaved` | client | mutation | Yes — Linux default-Cancel local-removal confirmation; Mac sheet planned | implemented ([LW:460](../../packaging/linux/desktop/connection_window.py#L460)) | planned (3b-2b, TASK-17661); unavailable on main ([MW:244](../../app/mac/Sources/HappyRanchApp/ConnectionWindow.swift#L244)) | `happyranch connection client remove-saved` | `src/client.ts:297` (Client / Remove saved access) | Linux #1689; web #1691; CLI #1705; no Mac landing |
| `client.disconnect` | client | mutation | Yes — Linux native stop confirmation; Mac sheet planned | implemented ([LW:476](../../packaging/linux/desktop/connection_window.py#L476)) | planned (3b-2b, TASK-17661); unavailable on main ([MW:244](../../app/mac/Sources/HappyRanchApp/ConnectionWindow.swift#L244)) | none (separate verb returns unavailable/notOwned) | `src/client.ts:473` (Client / Connected) | Linux #1675; web #1691; no Mac landing |
| `client.cancelAttempt` | client | mutation | Yes — Linux native stop confirmation/focus existing sheet; Mac sheet planned | implemented ([LW:477](../../packaging/linux/desktop/connection_window.py#L477)) | planned (3b-2b, TASK-17661); unavailable on main ([MW:244](../../app/mac/Sources/HappyRanchApp/ConnectionWindow.swift#L244)) | none (separate verb returns unavailable/notOwned) | `src/client.ts:468` (Client / in-flight Cancel) | Linux #1675, #1689 (Saving); web #1691; no Mac landing |
| `client.retry` | client | mutation | Yes — Linux reopens native paste/target ceremony; Mac sheet planned | implemented ([LW:467](../../packaging/linux/desktop/connection_window.py#L467)) | planned (3b-2b, TASK-17661); unavailable on main ([MW:244](../../app/mac/Sources/HappyRanchApp/ConnectionWindow.swift#L244)) | none (separate verb returns unavailable/notOwned) | `src/client.ts:469` (Client / Failed / Retry) | Linux #1675; web #1691; no Mac landing |
| `client.retrySave` | client | mutation | Linux: No unlock prompt requested; Mac: Yes, existing Keychain consent in #1668 | implemented ([LW:462](../../packaging/linux/desktop/connection_window.py#L462)) | in open PR #1668 (ordinary non-replacement save only); unavailable on main ([MW:244](../../app/mac/Sources/HappyRanchApp/ConnectionWindow.swift#L244); [#1668 source:318](https://github.com/t-benze/happyranch/blob/e49359ce62a0c2f326d3a2b34a07a637c0e370f4/app/mac/Sources/HappyRanchApp/ConnectionWindow.swift#L318)) | none (separate verb returns unavailable/notOwned) | `src/client.ts:454` (Client / Not saved / Retry save) | Linux #1689; web #1691; Mac pending #1668 |
| `client.health` | client | read | No | implemented ([LW:454](../../packaging/linux/desktop/connection_window.py#L454)) | in open PR #1668; unavailable on main ([MW:244](../../app/mac/Sources/HappyRanchApp/ConnectionWindow.swift#L244); [#1668 source:308](https://github.com/t-benze/happyranch/blob/e49359ce62a0c2f326d3a2b34a07a637c0e370f4/app/mac/Sources/HappyRanchApp/ConnectionWindow.swift#L308)) | none (separate verb returns unavailable/notOwned) | `src/client.ts:140`, `src/client.ts:472` (Client / health) | Linux #1675; web #1691; Mac pending #1668 |

### Events

| Event | Role | Read vs mutation | Native dialog/system prompt involved | Linux native status | Mac native status | CLI equivalent | Web screen (file/entry) | PR(s) that landed it |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `host.stateChanged` | host | read (event subscription) | No | implemented ([LW:537](../../packaging/linux/desktop/connection_window.py#L537)) | deferred (founder option B); no native emission ([MW:189](../../app/mac/Sources/HappyRanchApp/ConnectionWindow.swift#L189), [MS:38](../../app/mac/Sources/HappyRanchApp/ConnectionScripts.swift#L38)) | none | `src/main.ts:81` → `src/host.ts` (Host snapshot) | Linux #1696; schema #1647; host web #1694 |
| `client.stateChanged` | client | read (event subscription) | No | implemented ([LW:499](../../packaging/linux/desktop/connection_window.py#L499)) | in open PR #1668; no native emission on main ([MW:189](../../app/mac/Sources/HappyRanchApp/ConnectionWindow.swift#L189), [MS:38](../../app/mac/Sources/HappyRanchApp/ConnectionScripts.swift#L38); [#1668 source:370](https://github.com/t-benze/happyranch/blob/e49359ce62a0c2f326d3a2b34a07a637c0e370f4/app/mac/Sources/HappyRanchApp/ConnectionWindow.swift#L370)) | none | `src/main.ts:81` → `src/client.ts` (Client snapshot) | Linux #1675; schema #1647; client web #1691; Mac pending #1668 |

**Platform totals:** Linux **16/19** commands (bridge 1/1, host 5/8, client 10/10), **2/2** events. Mac **2/19** commands (bridge 1/1, host 0/8, client 1/10), **0/2** events.

### Prompt, CLI and provenance notes

- Linux [HOST_METHODS/HOST_MUTATIONS](../../packaging/linux/desktop/connection_contract.py#L23) distinguish the five working host methods from Enable/Disable/IssueInvite. Only approve/reject/revoke request [interactive authorization](../../packaging/linux/desktop/connection_contract.py#L411); `nextDevice` changes the per-bus selection cursor without changing access. The host web confirmation is additional to polkit.
- Linux native [paste/target](../../packaging/linux/desktop/client_ui.py#L74), [saved-target](../../packaging/linux/desktop/client_ui.py#L106) and [stop](../../packaging/linux/desktop/client_ui.py#L163) dialogs own consent. [Secret Service custody](../../packaging/linux/desktop/client_store.py#L85) does not request unlock/create or an UNLOCK search; locked/denied storage stays explicit. Linux saved discovery reads custody; Mac #1668 only projects existing native discovery.
- CLI grammar is in [`cli/commands/connection.py:10`](../../cli/commands/connection.py#L10). Enable/turn-off/create-invite are registered **unavailable**; [host fixed mappings](../../packaging/linux/desktop/connection_cli.py#L16) back the five device/status operations. Revoke selects and confirms on its own retained bus connection, not a cursor inherited from `next-device`.
- [Linux foreground client CLI](../../packaging/linux/desktop/client_cli.py#L162) backs invite accept, saved listing/connect/remove. Its status explicitly reports `notOwned`; separate disconnect/cancel/retry/retry-save/health verbs return `unavailable/notOwned` and never control GUI sessions. Ctrl-C/SIGTERM stops the owning foreground invocation. Runtime reachable is not verified loaded RELEASE UI.
- PR provenance was checked with `git log`/`git blame`: schema/facade #1647; Linux loader/read-only host #1648; Mac loader/read-only client #1654; Linux client lifecycle/events #1675; Linux saved access #1689; client screens #1691; host screens #1694; fixed host mutations/events #1696; host CLI #1698; foreground client CLI #1705. Pending #1668 is not a landing.

## Planned, not in v1 schema

The following names are **reserved proposals**, not callable commands. Linux lifecycle integration is planned under THR-327 / TASK-17385 (slice 2b); the spec retains historical backend references to TASK-16779. Mac host remains deferred under founder option B.

| Capability | Spec quote and section | Schema gap / required mapping |
| --- | --- | --- |
| Host invite cancel/replacement (H09) | §7: “Host invite invalidation/replacement (H09) … are required for B”; §8 H09: “explicit host cancellation lacks a supported method.” | Proposed `host.cancelInvite` is absent. Replacement must invalidate prior invite/pending authority; closing the native sheet only hides it. |
| Recognized FAILED-SETUP replacement / interrupted recovery (H04/X02) | §8 H04: “No helper method or recognized-replacement result”; §7: “Act only on positively recognized owned state.” | Proposed `host.replaceFailed` is absent. Preserve runtime/org data and unrelated installations; unknown residue remains operator recovery. |
| H20 address refresh | §8 H20: “Host address observation and fixed RefreshHomeAddress absent at seam.” | Proposed `host.refreshHomeAddress` is absent. §7/§9 require native address derivation, fresh authorization, old-invite invalidation and a fresh invite with updated details. |
| Policy-renewal recovery (H19/X07) | §8 H19: “renewal cause/deadline and RetryPolicyRenewal are absent at seam.” | Proposed `host.retryPolicyRenewal` is absent. §7 limits renewal to the identical valid shipped policy while preserving revocations. |

[Spec §7](2026-11-17-web-native-bridge.md#internal-transport-schema) explicitly says these four proposed names “are **not v1 callable names**”; §9 requires versioned fixed CLI mappings too. Registered CLI recovery placeholders remain unavailable, not implementations.

Additional §7–10 findings, **not additional approved schema commands**:

- **Client C19 metadata-only recovery:** §8 says “explicit native confirmation needed for updated metadata.” There is no standalone C19 command. §7/§9 require the same home/CA/node and live saved grant, no unused join/pair redemption, and no claim of Host-wide invalidation; its mapping must remain visible in lifecycle delivery.
- **Host setup cancellation:** no standalone cancel-setup command. §8 H03 says “No Enable stage/readiness transaction API at helper seam”; cancellation/permission denial belongs to the native Enable ceremony rather than an invented web action.
- **Keep-running/runtime controls (H18/A14):** §8 says “Outside this schema/helper.” §7 says they “stay separate app functions”; their parity is required, not a pending v1 command.
- **Signed storage migration (X08):** §8 marks it “Deferred annotation only”, not a v1 capability or unsigned-reconnect prerequisite.

## Maintenance

Any PR that changes the schema command/event list or a platform's implementation of a command updates this inventory in the same PR.

Re-derive the closed lists, inspect both platform dispatchers and event producers, refresh CLI/prompt/web mappings, and use git history for landing PRs. Update the full base SHA/date and totals; distinguish main from open PRs and source implementation from native acceptance.
