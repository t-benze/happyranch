# Linux connection client

The packaged connection view and `happyranch connection client accept-invite`
support C19 same-home address recovery. When exactly one saved record matches
the home, control plane, connector and CA, changed target metadata requires a
native confirmation (or typing `update` on the controlling terminal).

Recovery verifies the saved node and live grant without redeeming the new
invite's join/pair material, then uses the existing login-keyring save/readback.
Failed validation requires separate fresh consent and normal Host approval;
declining launches nothing. The client makes no Host-wide invite invalidation claim.

The foreground CLI connects/recovers and observes access only. It calls
`ClientSession.health`, then checks access at one-second intervals until
Ctrl-C/SIGTERM or denial. It creates, prints, saves and retains no client runtime
port/URL and does not open a browser runtime. `runtimeReachable` confirms health,
never that the RELEASE UI loaded. Terminal copy follows the native language
preference: “Runtime reachable.” / “运行时可连接。” and “Observing access;
Ctrl-C disconnects.” / “正在检查访问权限；按 Ctrl-C 断开连接。”.
Local `remove-saved` confirms only saved-access removal, never Host revocation.

See the [Linux release handbook](../../docs/operations/linux-release-handbook.md)
for supported standalone checks, custody requirements and foreground CLI usage.
Real-network C19 acceptance is slice 7 **NOT RUN**.
