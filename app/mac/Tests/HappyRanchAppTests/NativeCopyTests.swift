import XCTest
import Foundation
import Darwin
import HappyRanchSupervisor
@testable import HappyRanchApp

final class NativeCopyTests: XCTestCase {
    private func directory() throws -> URL {
        // Foundation can strip /private and reintroduce the /var symlink.
        // The production stores deliberately refuse symlinked parent paths.
        let physical = try XCTUnwrap(realpath(FileManager.default.temporaryDirectory.path, nil))
        defer { free(physical) }
        let root = URL(fileURLWithFileSystemRepresentation: physical, isDirectory: true, relativeTo: nil)
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        return root
    }

    @MainActor
    func testNativeSurfaceCopyUsesImmutableLaunchLocale() throws {
        let root = try directory()
        defer { try? FileManager.default.removeItem(at: root) }
        let file = AppPreferenceFile(directory: root, name: "locale")
        // Independent literal expectations for every newly localized native key.
        let cases: [(NativeCopy, String, String)] = [
            (.runtimeReachable, "Runtime reachable.", "运行时可连接。"),
            (.observingAccess, "Observing access; Ctrl-C disconnects.", "正在检查访问权限；按 Ctrl-C 断开连接。"),
            (.diagnostics, "Diagnostics", "诊断"),
            (.exportBundle, "Export Bundle", "导出诊断包"),
            (.app, "App", "应用"),
            (.version, "Version", "版本"),
            (.build, "Build", "构建"),
            (.architecture, "Architecture", "架构"),
            (.buildSHA, "Build SHA", "构建 SHA"),
            (.runtimeHome, "Runtime Home", "运行时主目录"),
            (.runtimePath, "Runtime Path", "运行时路径"),
            (.startCommand, "Start Command", "启动命令"),
            (.updates, "Updates", "更新"),
            (.daemon, "Daemon", "后台服务"),
            (.state, "State", "状态"),
            (.port, "Port", "端口"),
            (.bindHost, "Bind Host", "绑定主机"),
            (.lastExitCode, "Last Exit Code", "上次退出代码"),
            (.lastSignal, "Last Signal", "上次信号"),
            (.daemonStdout, "Daemon Stdout (captured)", "后台服务标准输出（已捕获）"),
            (.daemonStderr, "Daemon Stderr (captured)", "后台服务标准错误（已捕获）"),
            (.managedHealth, "Managed Connection Health (latest cached record)", "托管连接健康状态（最新缓存记录）"),
            (.healthProbe, "Health Probe", "健康检查"),
            (.result, "Result:", "结果："),
            (.success, "Success", "成功"),
            (.failed, "Failed", "失败"),
            (.latency, "Latency", "延迟"),
            (.error, "Error", "错误"),
            (.daemonLog, "Daemon Log (tail, redacted)", "后台服务日志（末尾，已脱敏）"),
            (.launcherLog, "Launcher Log (redacted)", "启动器日志（已脱敏）"),
            (.diagnosticsLocation, "Diagnostics Location", "诊断文件位置"),
            (.path, "Path:", "路径："),
            (.environmentSummary, "Environment Summary (redacted)", "环境摘要（已脱敏）"),
            (.daemonCrashed, "Daemon Crashed", "后台服务已崩溃"),
            (.daemonFailed, "Daemon Failed", "后台服务失败"),
            (.crashGuidance, "Review the captured stderr and diagnostics below to identify the cause.", "请查看下方捕获的标准错误和诊断信息，以确定原因。"),
            (.exportDiagnostics, "Export Diagnostics", "导出诊断信息"),
            (.exported, "Exported to {value}", "已导出至 {value}"),
            (.exportFailed, "Export failed: {value}", "导出失败：{value}"),
            (.daemonIssue, "Daemon issue", "后台服务出现问题"),
            (.unhealthyGuidance, "Health check is failing — daemon may recover on its own.", "健康检查失败，后台服务可能自行恢复。"),
            (.failedGuidance, "Daemon has failed and needs a restart.", "后台服务已失败，需要重新启动。"),
            (.restartDaemon, "Restart Daemon", "重启后台服务"),
            (.done, "Done", "完成"),
            (.remoteAccessHome, "Remote Access (Home)", "远程访问（本机）"),
            (.tailnetAddress, "Tailnet address: {value}", "Tailnet 地址：{value}"),
            (.startConnector, "Start Connector", "启动连接器"),
            (.stopConnector, "Stop Connector", "停止连接器"),
            (.listeningPort, "Listening on port {value}", "正在监听端口 {value}"),
            (.devicePairing, "Device Pairing", "设备配对"),
            (.generatePairingCode, "Generate Pairing Code", "生成配对码"),
            (.pairingCodeLabel, "Pairing code:", "配对码："),
            (.shareAddress, "Share: {value}", "分享：{value}"),
            (.copyAddress, "Copy Address", "复制地址"),
            (.pairingExpiry, "Code is one-time use and expires in 5 minutes", "配对码仅可使用一次，5 分钟后过期"),
            (.pairedDevices, "Paired Devices", "已配对设备"),
            (.noPairedDevices, "No paired devices", "暂无已配对设备"),
            (.revoke, "Revoke", "撤销"),
            (.connection, "Connection", "连接"),
            (.homeInvite, "Home invite", "家庭电脑邀请"),
            (.existingDIY, "Existing DIY Tailscale", "现有自建 Tailscale"),
            (.connectComputer, "Connect to another computer", "连接到另一台电脑"),
            (.controlHost, "Control host: {value}", "控制主机：{value}"),
            (.homeAddress, "Home address: {value}", "家庭电脑地址：{value}"),
            (.homeIdentity, "Home identity: {value}", "家庭电脑身份：{value}"),
            (.releaseRuntime, "Release runtime: {value}", "发布版运行时：{value}"),
            (.inviteExpiry, "Invite expires in {value}", "邀请将在 {value} 后过期"),
            (.waitingApproval, "Waiting for approval on {value}", "正在等待 {value} 上的批准"),
            (.yourHomeComputer, "your home computer", "你的家庭电脑"),
            (.comparePhrase, "Compare this phrase on Linux, then approve this device there.", "请在 Linux 上核对这段短语，然后在那里批准此设备。"),
            (.pasteInvite, "Paste one invite from Linux", "粘贴一份 Linux 邀请"),
            (.trustInvite, "Trust an invite only from your home computer. Its sender selects the control host and home CA.", "仅信任来自你家庭电脑的邀请。发送者会选择控制主机和家庭电脑 CA。"),
            (.connect, "Connect", "连接"),
            (.freshAccessConsent, "Saved access could not be opened. Create new local access after fresh Linux approval? The obsolete host grant remains revocable on Linux; the old item is never silently replaced.", "无法打开已保存的访问权限。是否在 Linux 重新批准后创建新的本地访问权限？旧主机授权仍可在 Linux 上撤销；旧项目绝不会被静默替换。"),
            (.continueFreshApproval, "Continue with fresh approval", "继续并重新批准"),
            (.cancel, "Cancel", "取消"),
            (.retry, "Retry", "重试"),
            (.disconnect, "Disconnect", "断开连接"),
            (.disconnectKeepsAccess, "Disconnect keeps saved access.", "断开连接会保留已保存的访问权限。"),
            (.replaceSavedAccessPrompt, "Replace saved access…", "替换已保存的访问权限…"),
            (.replaceAccessConsent, "Replace the old local item with this freshly approved device? The old host grant remains revocable on Linux.", "是否用此新批准的设备替换旧的本地项目？旧主机授权仍可在 Linux 上撤销。"),
            (.replaceSavedAccess, "Replace saved access", "替换已保存的访问权限"),
            (.retrySaving, "Retry saving", "重试保存"),
            (.someAccessUnavailable, "Some saved access is unavailable. Other approved computers can still reconnect.", "部分已保存的访问权限不可用。其他已批准的电脑仍可重新连接。"),
            (.savedComputer, "Saved computer", "已保存的电脑"),
            (.reconnect, "Reconnect", "重新连接"),
            (.removeComputerPrompt, "Remove saved computer…", "移除已保存的电脑…"),
            (.removeConsent, "Erase local access? This does not revoke the device on Linux.", "是否删除本地访问权限？这不会撤销 Linux 上的设备授权。"),
            (.removeComputer, "Remove saved computer", "移除已保存的电脑"),
            (.removalIncomplete, "Removal incomplete", "移除未完成"),
            (.retryRemoval, "Retry removal", "重试移除"),
            (.connecting, "Connecting…", "正在连接…"),
            (.homeTailnetAddress, "Home Tailnet Address", "家庭电脑 Tailnet 地址"),
            (.homeConnectorPort, "Home Connector Port", "家庭电脑连接器端口"),
            (.pairingCode, "Pairing Code", "配对码"),
            (.connected, "Connected", "已连接"),
            (.disconnected, "Disconnected", "已断开连接"),
            (.idle, "Idle", "空闲"),
            (.checkingSavedAccess, "Checking saved access in login Keychain", "正在登录钥匙串中检查已保存的访问权限"),
            (.validatingInvite, "Validating invite", "正在验证邀请"),
            (.joiningNetwork, "Joining network", "正在加入网络"),
            (.pairing, "Pairing", "正在配对"),
            (.awaitingLinuxApproval, "Awaiting Linux approval", "正在等待 Linux 批准"),
            (.savingAccess, "Saving access in login Keychain", "正在登录钥匙串中保存访问权限"),
            (.openingRuntime, "Opening runtime", "正在打开运行时"),
            (.readyToSwitch, "Ready to switch", "可以切换"),
            (.busy, "Busy", "忙碌"),
            (.closingSession, "Closing local session; waiting for any storage write/readback to settle", "正在关闭本地会话；等待存储写入和回读完成"),
            (.savingUnconfirmed, "Saving was not confirmed. Retry checks saved access again; a new invite may be needed.", "保存尚未确认。重试会再次检查已保存的访问权限；可能需要新邀请。"),
            (.retrySaveGuidance, "Retry saving while this session is open. If the app quits before saving, create a new invite on Linux.", "请在此会话仍打开时重试保存。如果应用在保存前退出，请在 Linux 上创建新邀请。"),
            (.newInviteGuidance, "Create a new invite on Linux. Retry accepts a new invite; the spent key is never reused.", "请在 Linux 上创建新邀请。重试会接受新邀请；已消耗的密钥绝不会重复使用。"),
            (.networkRetryGuidance, "Connect to your home network / Retry. Saved access needs no new invite while its grant remains valid.", "请连接家庭网络 / 重试。只要授权仍有效，已保存的访问权限就无需新邀请。"),
            (.reapprovalRequired, "New invite and approval required", "需要新邀请和批准"),
            (.malformedInvite, "Invite could not be validated — Create new invite on Linux", "无法验证邀请 — 请在 Linux 上创建新邀请"),
            (.expiredInvite, "Invite expired — Create new invite on Linux", "邀请已过期 — 请在 Linux 上创建新邀请"),
            (.homeUnavailable, "Not on your home network, or home computer unavailable", "未连接家庭网络，或家庭电脑不可用"),
            (.joinDenied, "Network join did not finish — Create new invite on Linux", "加入网络未完成 — 请在 Linux 上创建新邀请"),
            (.identityUnavailable, "Device identity unavailable — Create new invite on Linux", "设备身份不可用 — 请在 Linux 上创建新邀请"),
            (.approvalDenied, "Linux approval denied or expired — Create new invite on Linux", "Linux 批准已被拒绝或已过期 — 请在 Linux 上创建新邀请"),
            (.authorizationIncomplete, "Authorization did not finish — Create a new invite on Linux", "授权未完成 — 请在 Linux 上创建新邀请"),
            (.revoked, "Access revoked — Create new invite on Linux", "访问权限已撤销 — 请在 Linux 上创建新邀请"),
            (.daemonUnavailable, "Home HappyRanch is offline/restarting — Retry", "家庭电脑上的 HappyRanch 已离线或正在重启 — 请重试"),
            (.identityChanged, "Home identity changed — approve again", "家庭电脑身份已更改 — 请重新批准"),
            (.addressChanged, "Home address changed — Refresh home address on Linux and accept a fresh invite. If device identity cannot be retained, approve again.", "家庭电脑地址已更改 — 请在 Linux 上刷新家庭电脑地址并接受新邀请。如果无法保留设备身份，请重新批准。"),
            (.cleanupIncomplete, "Local cleanup incomplete — Retry removal", "本地清理未完成 — 请重试移除"),
            (.savedPermission, "Saved access needs permission — Retry", "已保存的访问权限需要许可 — 请重试"),
            (.unlockKeychain, "Unlock login Keychain and retry", "请解锁登录钥匙串并重试"),
            (.savedIdentityChanged, "Saved access needs Keychain permission after this update — Unlock/Allow and retry", "此次更新后，已保存的访问权限需要钥匙串许可 — 请解锁 / 允许并重试"),
            (.accessNotSaved, "Access was not saved — Retry", "访问权限未保存 — 请重试"),
            (.deletionFailed, "Removal incomplete — Retry removal", "移除未完成 — 请重试移除"),
            (.savedUnavailable, "Saved access unavailable — create a new invite on Linux", "已保存的访问权限不可用 — 请在 Linux 上创建新邀请"),
            (.removalInterrupted, "Local removal was interrupted; Retry removal for this computer", "本地移除已中断；请重试移除此电脑"),
            (.sessionStopUnconfirmed, "Local session could not be confirmed stopped", "无法确认本地会话已停止"),
            (.removalUnconfirmed, "Saved access changed or could not be read; removal was not confirmed", "已保存的访问权限已更改或无法读取；移除尚未确认"),
            (.networkEraseFailed, "Saved network state could not be erased", "无法删除已保存的网络状态"),
            (.keychainRetained, "Login Keychain access retained for Retry removal", "已保留登录钥匙串访问权限，以便重试移除"),
            (.bearerEraseFailed, "Login Keychain bearer and trust could not be erased", "无法删除登录钥匙串中的持有者凭据和信任信息"),
            (.fillAllFields, "Please fill in all fields", "请填写所有字段"),
            (.pairingRefused, "Refused — invalid or expired pairing code", "已拒绝 — 配对码无效或已过期"),
            (.invalidConnectorResponse, "Invalid response from home connector", "家庭电脑连接器返回的响应无效"),
            (.pairingIncomplete, "Pairing did not complete. Request fresh pairing approval.", "配对未完成。请请求新的配对批准。"),
            (.connectionFailed, "Connection failed: {value}", "连接失败：{value}"),
            (.unexpectedError, "Unexpected error: {value}", "意外错误：{value}"),
            (.previousDaemonRunning, "Cannot start: the previous managed daemon is still running", "无法启动：先前托管的后台服务仍在运行"),
            (.externalStopConfirmation, "Stop requires confirmation for external daemon", "停止外部后台服务需要确认"),
            (.errorDetail, "Error: {value}", "错误：{value}"),
            (.launchFailed, "Launch failed: {value}", "启动失败：{value}"),
            (.stopIncomplete, "Stop incomplete: daemon still running; SIGKILL errno: {value}", "停止未完成：后台服务仍在运行；SIGKILL errno：{value}"),
            (.notConfigured, "notConfigured", "未配置"),
            (.stopped, "stopped", "已停止"),
            (.externalRunning, "externalRunning", "外部服务运行中"),
            (.starting, "starting", "正在启动"),
            (.running, "running", "运行中"),
            (.unhealthy, "unhealthy", "健康检查异常"),
            (.stalePid, "stalePid", "过期 PID"),
            (.stopping, "stopping", "正在停止"),
            (.crashed, "crashed", "已崩溃"),
            (.daemonFailedState, "failed", "失败")
        ]
        for language in ["en", "zh-CN"] {
            try file.write(language)
            let preference = AppLocalePreference(directory: root)
            let nextLanguage = language == "en" ? "zh-CN" : "en"
            for (copy, english, chinese) in cases {
                XCTAssertEqual(copy.text(locale: preference), language == "en" ? english : chinese, "\(copy)")
            }
            preference.call("{\"schema\":2,\"locale\":\"\(nextLanguage)\"}", current: { true },
                            reply: { _, error in XCTAssertNil(error) }, publish: { $0() })
            XCTAssertEqual(preference.snapshot.current, nextLanguage)
            let nextLaunch = AppLocalePreference(directory: root)
            for (copy, english, chinese) in cases {
                XCTAssertEqual(copy.text(locale: preference), language == "en" ? english : chinese,
                               "live changes cannot alter native copy: \(copy)")
                XCTAssertEqual(copy.text(locale: nextLaunch), nextLanguage == "en" ? english : chinese,
                               "next launch adopts the confirmed choice: \(copy)")
            }
        }
    }

    @MainActor
    func testNativeFormattedCopyPreservesRawValues() throws {
        let root = try directory()
        defer { try? FileManager.default.removeItem(at: root) }
        let file = AppPreferenceFile(directory: root, name: "locale")
        // Synthetic raw content includes a placeholder token: insertion must not reinterpret it.
        let value = "fixture-{value}-E42-原文"
        let cases: [(NativeCopy, String, String)] = [
            (.exported, "Exported to fixture-{value}-E42-原文", "已导出至 fixture-{value}-E42-原文"),
            (.exportFailed, "Export failed: fixture-{value}-E42-原文", "导出失败：fixture-{value}-E42-原文"),
            (.tailnetAddress, "Tailnet address: fixture-{value}-E42-原文", "Tailnet 地址：fixture-{value}-E42-原文"),
            (.listeningPort, "Listening on port fixture-{value}-E42-原文", "正在监听端口 fixture-{value}-E42-原文"),
            (.shareAddress, "Share: fixture-{value}-E42-原文", "分享：fixture-{value}-E42-原文"),
            (.controlHost, "Control host: fixture-{value}-E42-原文", "控制主机：fixture-{value}-E42-原文"),
            (.homeAddress, "Home address: fixture-{value}-E42-原文", "家庭电脑地址：fixture-{value}-E42-原文"),
            (.homeIdentity, "Home identity: fixture-{value}-E42-原文", "家庭电脑身份：fixture-{value}-E42-原文"),
            (.releaseRuntime, "Release runtime: fixture-{value}-E42-原文", "发布版运行时：fixture-{value}-E42-原文"),
            (.inviteExpiry, "Invite expires in fixture-{value}-E42-原文", "邀请将在 fixture-{value}-E42-原文 后过期"),
            (.waitingApproval, "Waiting for approval on fixture-{value}-E42-原文", "正在等待 fixture-{value}-E42-原文 上的批准"),
            (.connectionFailed, "Connection failed: fixture-{value}-E42-原文", "连接失败：fixture-{value}-E42-原文"),
            (.unexpectedError, "Unexpected error: fixture-{value}-E42-原文", "意外错误：fixture-{value}-E42-原文"),
            (.errorDetail, "Error: fixture-{value}-E42-原文", "错误：fixture-{value}-E42-原文"),
            (.launchFailed, "Launch failed: fixture-{value}-E42-原文", "启动失败：fixture-{value}-E42-原文"),
            (.stopIncomplete, "Stop incomplete: daemon still running; SIGKILL errno: fixture-{value}-E42-原文", "停止未完成：后台服务仍在运行；SIGKILL errno：fixture-{value}-E42-原文")
        ]
        for language in ["en", "zh-CN"] {
            try file.write(language)
            let preference = AppLocalePreference(directory: root)
            for (copy, english, chinese) in cases {
                XCTAssertEqual(copy.format(value: value, locale: preference), language == "en" ? english : chinese)
            }
        }
    }

    @MainActor
    func testNativeModelMessagesAndRawDiagnosticsStayDistinct() throws {
        let root = try directory()
        defer { try? FileManager.default.removeItem(at: root) }
        let file = AppPreferenceFile(directory: root, name: "locale")
        let phases: [(HomeInviteModel.Phase, String, String)] = [
            (.idle, "Idle", "空闲"),
            (.checkingSavedAccess, "Checking saved access in login Keychain", "正在登录钥匙串中检查已保存的访问权限"),
            (.validating, "Validating invite", "正在验证邀请"),
            (.joining, "Joining network", "正在加入网络"),
            (.pairing, "Pairing", "正在配对"),
            (.awaitingApproval, "Awaiting Linux approval", "正在等待 Linux 批准"),
            (.saving, "Saving access in login Keychain", "正在登录钥匙串中保存访问权限"),
            (.opening, "Opening runtime", "正在打开运行时"),
            (.readyToSwitch, "Ready to switch", "可以切换"),
            (.connected, "Connected", "已连接"),
            (.runtimeBusy, "Busy", "忙碌"),
            (.stopping, "Closing local session; waiting for any storage write/readback to settle", "正在关闭本地会话；等待存储写入和回读完成"),
            (.failed(.malformedInvite), "Invite could not be validated — Create new invite on Linux", "无法验证邀请 — 请在 Linux 上创建新邀请"),
            (.failed(.expiredInvite), "Invite expired — Create new invite on Linux", "邀请已过期 — 请在 Linux 上创建新邀请"),
            (.failed(.controlUnreachable), "Not on your home network, or home computer unavailable", "未连接家庭网络，或家庭电脑不可用"),
            (.failed(.joinDenied), "Network join did not finish — Create new invite on Linux", "加入网络未完成 — 请在 Linux 上创建新邀请"),
            (.failed(.homeUnavailable), "Not on your home network, or home computer unavailable", "未连接家庭网络，或家庭电脑不可用"),
            (.failed(.identityUnavailable), "Device identity unavailable — Create new invite on Linux", "设备身份不可用 — 请在 Linux 上创建新邀请"),
            (.failed(.approvalDenied), "Linux approval denied or expired — Create new invite on Linux", "Linux 批准已被拒绝或已过期 — 请在 Linux 上创建新邀请"),
            (.failed(.authorizationIncomplete), "Authorization did not finish — Create a new invite on Linux", "授权未完成 — 请在 Linux 上创建新邀请"),
            (.failed(.revoked), "Access revoked — Create new invite on Linux", "访问权限已撤销 — 请在 Linux 上创建新邀请"),
            (.failed(.daemonUnavailable), "Home HappyRanch is offline/restarting — Retry", "家庭电脑上的 HappyRanch 已离线或正在重启 — 请重试"),
            (.failed(.identityChanged), "Home identity changed — approve again", "家庭电脑身份已更改 — 请重新批准"),
            (.failed(.addressChanged), "Home address changed — Refresh home address on Linux and accept a fresh invite. If device identity cannot be retained, approve again.", "家庭电脑地址已更改 — 请在 Linux 上刷新家庭电脑地址并接受新邀请。如果无法保留设备身份，请重新批准。"),
            (.failed(.cleanupIncomplete), "Local cleanup incomplete — Retry removal", "本地清理未完成 — 请重试移除"),
            (.failed(.reapprovalRequired), "New invite and approval required", "需要新邀请和批准"),
            (.keychain(.reapprovalRequired), "New invite and approval required", "需要新邀请和批准"),
            (.keychain(.notFound), "Saved access unavailable — create a new invite on Linux", "已保存的访问权限不可用 — 请在 Linux 上创建新邀请"),
            (.keychain(.cancelled), "Saved access needs permission — Retry", "已保存的访问权限需要许可 — 请重试"),
            (.keychain(.denied), "Saved access unavailable — create a new invite on Linux", "已保存的访问权限不可用 — 请在 Linux 上创建新邀请"),
            (.keychain(.locked), "Unlock login Keychain and retry", "请解锁登录钥匙串并重试"),
            (.keychain(.identityChanged), "Saved access needs Keychain permission after this update — Unlock/Allow and retry", "此次更新后，已保存的访问权限需要钥匙串许可 — 请解锁 / 允许并重试"),
            (.keychain(.invalidRecord), "Saved access unavailable — create a new invite on Linux", "已保存的访问权限不可用 — 请在 Linux 上创建新邀请"),
            (.keychain(.saveFailed), "Access was not saved — Retry", "访问权限未保存 — 请重试"),
            (.keychain(.deletionFailed), "Removal incomplete — Retry removal", "移除未完成 — 请重试移除"),
            (.keychain(.unavailable), "Saved access unavailable — create a new invite on Linux", "已保存的访问权限不可用 — 请在 Linux 上创建新邀请")
        ]
        let messages: [(String, String, String)] = [
            ("Saving was not confirmed. Retry checks saved access again; a new invite may be needed.", "Saving was not confirmed. Retry checks saved access again; a new invite may be needed.", "保存尚未确认。重试会再次检查已保存的访问权限；可能需要新邀请。"),
            ("Retry saving while this session is open. If the app quits before saving, create a new invite on Linux.", "Retry saving while this session is open. If the app quits before saving, create a new invite on Linux.", "请在此会话仍打开时重试保存。如果应用在保存前退出，请在 Linux 上创建新邀请。"),
            ("Create a new invite on Linux. Retry accepts a new invite; the spent key is never reused.", "Create a new invite on Linux. Retry accepts a new invite; the spent key is never reused.", "请在 Linux 上创建新邀请。重试会接受新邀请；已消耗的密钥绝不会重复使用。"),
            ("Connect to your home network / Retry. Saved access needs no new invite while its grant remains valid.", "Connect to your home network / Retry. Saved access needs no new invite while its grant remains valid.", "请连接家庭网络 / 重试。只要授权仍有效，已保存的访问权限就无需新邀请。"),
            ("Local removal was interrupted; Retry removal for this computer", "Local removal was interrupted; Retry removal for this computer", "本地移除已中断；请重试移除此电脑"),
            ("Local session could not be confirmed stopped", "Local session could not be confirmed stopped", "无法确认本地会话已停止"),
            ("Saved access changed or could not be read; removal was not confirmed", "Saved access changed or could not be read; removal was not confirmed", "已保存的访问权限已更改或无法读取；移除尚未确认"),
            ("Saved network state could not be erased", "Saved network state could not be erased", "无法删除已保存的网络状态"),
            ("Login Keychain access retained for Retry removal", "Login Keychain access retained for Retry removal", "已保留登录钥匙串访问权限，以便重试移除"),
            ("Login Keychain bearer and trust could not be erased", "Login Keychain bearer and trust could not be erased", "无法删除登录钥匙串中的持有者凭据和信任信息"),
            ("Please fill in all fields", "Please fill in all fields", "请填写所有字段"),
            ("Refused — invalid or expired pairing code", "Refused — invalid or expired pairing code", "已拒绝 — 配对码无效或已过期"),
            ("Invalid response from home connector", "Invalid response from home connector", "家庭电脑连接器返回的响应无效"),
            ("Pairing did not complete. Request fresh pairing approval.", "Pairing did not complete. Request fresh pairing approval.", "配对未完成。请请求新的配对批准。"),
            ("Cannot start: the previous managed daemon is still running", "Cannot start: the previous managed daemon is still running", "无法启动：先前托管的后台服务仍在运行"),
            ("Stop requires confirmation for external daemon", "Stop requires confirmation for external daemon", "停止外部后台服务需要确认"),
            ("notConfigured", "notConfigured", "未配置"),
            ("stopped", "stopped", "已停止"),
            ("externalRunning", "externalRunning", "外部服务运行中"),
            ("starting", "starting", "正在启动"),
            ("running", "running", "运行中"),
            ("unhealthy", "unhealthy", "健康检查异常"),
            ("stalePid", "stalePid", "过期 PID"),
            ("stopping", "stopping", "正在停止"),
            ("crashed", "crashed", "已崩溃"),
            ("failed", "failed", "失败"),
            ("Connection failed: fixture-E42: 原文", "Connection failed: fixture-E42: 原文", "连接失败：fixture-E42: 原文"),
            ("Unexpected error: fixture-E42: 原文", "Unexpected error: fixture-E42: 原文", "意外错误：fixture-E42: 原文"),
            ("Error: fixture-E42: 原文", "Error: fixture-E42: 原文", "错误：fixture-E42: 原文"),
            ("Launch failed: fixture-E42: 原文", "Launch failed: fixture-E42: 原文", "启动失败：fixture-E42: 原文"),
            ("Stop incomplete: daemon still running; SIGKILL errno: fixture-E42: 原文", "Stop incomplete: daemon still running; SIGKILL errno: fixture-E42: 原文", "停止未完成：后台服务仍在运行；SIGKILL errno：fixture-E42: 原文")
        ]
        for language in ["en", "zh-CN"] {
            try file.write(language)
            let preference = AppLocalePreference(directory: root)
            for (phase, english, chinese) in phases {
                XCTAssertEqual(NativeCopy.phaseMessage(phase, locale: preference), language == "en" ? english : chinese)
            }
            for (input, english, chinese) in messages {
                XCTAssertEqual(NativeCopy.localize(input, locale: preference), language == "en" ? english : chinese)
            }
            for raw in ["owner_save_unknown", "removalIncomplete", "stateChanged", "E42 raw diagnostic 原文", "fixture-host:4242"] {
                XCTAssertEqual(NativeCopy.localize(raw, locale: preference), raw, "codes and raw diagnostics stay verbatim")
            }
        }
    }
}
