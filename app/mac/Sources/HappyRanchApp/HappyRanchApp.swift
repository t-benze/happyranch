import SwiftUI
import AppKit
import HappyRanchSupervisor

struct HappyRanchApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var appDelegate

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(appDelegate)
                .frame(minWidth: 720, minHeight: 440)
        }
        .windowResizability(.contentMinSize)
        .commands {
            CommandGroup(replacing: .newItem) {}

            CommandGroup(after: .appInfo) {
                Button(AppLocalePreference.shared.nativeChinese ? "检查更新…" : "Check for Updates…") { appDelegate.checkForUpdates() }
                    .disabled(!appDelegate.canCheckForUpdates || appDelegate.hasPendingUpdate)
                if appDelegate.hasPendingUpdate {
                    Button(AppLocalePreference.shared.nativeChinese ? "安装已下载的更新…" : "Install Downloaded Update…") { appDelegate.resumePendingUpdate() }
                }
            }

            CommandGroup(replacing: .appSettings) {
                Button(AppLocalePreference.shared.nativeChinese ? "设置…" : "Settings…") {
                    appDelegate.showSettings()
                }
                .keyboardShortcut(",", modifiers: .command)
                Button(AppLocalePreference.shared.nativeChinese ? "连接…" : "Connection…") {
                    appDelegate.showConnection()
                }
            }

            CommandMenu(AppLocalePreference.shared.nativeChinese ? "后台服务" : "Daemon") {
                Button(AppLocalePreference.shared.nativeChinese ? "启动后台服务" : "Start Daemon") {
                    appDelegate.startDaemon()
                }
                .disabled(!appDelegate.canStart)

                Button(AppLocalePreference.shared.nativeChinese ? "停止后台服务" : "Stop Daemon") {
                    appDelegate.stopDaemonWithConfirmation()
                }
                .disabled(!appDelegate.canStop)

                Button(AppLocalePreference.shared.nativeChinese ? "重启后台服务" : "Restart Daemon") {
                    appDelegate.restartDaemon()
                }
                .disabled(!appDelegate.canRestart)

                Divider()

                Button(AppLocalePreference.shared.nativeChinese ? "显示远程访问…" : "Show Remote Access…") {
                    appDelegate.showRemoteAccess = true
                }

                Divider()

                Button(appDelegate.switchRoleMenuLabel) {
                    confirmAndSwitchRole(appDelegate)
                }

                Divider()

                Button(AppLocalePreference.shared.nativeChinese ? "显示诊断…" : "Show Diagnostics…") {
                    appDelegate.showDiagnostics = true
                }

                Divider()

                // Status line — disabled to appear as a non-interactive indicator
                Text("\(AppLocalePreference.shared.nativeChinese ? "状态" : "Status"): \(NativeCopy.localize(appDelegate.stateText))")
            }
        }
    }

    /// Execute a role switch without UI confirmation.
    /// Test seam extracted from confirmAndSwitchRole to prove the menu/action
    /// path reaches AppDelegate.switchConnectionRole — a regression that removes
    /// the menu item or disconnects confirmAndSwitchRole from switchConnectionRole
    /// would still pass the direct-call suite without this seam.
    func executeRoleSwitch(from currentRole: ConnectionRole, in appDelegate: AppDelegate) {
        let target: ConnectionRolePreference = currentRole == .home ? .client : .home
        appDelegate.switchConnectionRole(to: target)
    }

    /// Present an NSAlert confirmation dialog for role switching.
    /// Returns true iff the user clicks "Switch".
    static func presentRoleSwitchAlert(title: String, body: String) -> Bool {
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = body
        alert.alertStyle = .warning
        alert.addButton(withTitle: AppLocalePreference.shared.nativeChinese ? "切换" : "Switch")
        alert.addButton(withTitle: AppLocalePreference.shared.nativeChinese ? "取消" : "Cancel")
        return alert.runModal() == .alertFirstButtonReturn
    }

    /// Confirm and execute a role switch, gated by a confirmation callback.
    /// Called from the "Connect to another computer…" / "Run HappyRanch on this Mac…" menu item.
    /// - Parameters:
    ///   - appDelegate: The AppDelegate on which to operate.
    ///   - confirmed: A closure that receives the alert title and body, and
    ///     returns true if the switch should proceed. Defaults to the real
    ///     NSAlert presentation; inject a stub in tests to bypass the modal.
    func confirmAndSwitchRole(
        _ appDelegate: AppDelegate,
        confirmed: @MainActor (String, String) -> Bool = HappyRanchApp.presentRoleSwitchAlert
    ) {
        let title = (AppLocalePreference.shared.nativeChinese ? "更改连接角色" : "Change Connection Role")
        let body: String

        if appDelegate.roleSwitchRequiresTeardown {
            body = appDelegate.connectionRole == .home
                ? (AppLocalePreference.shared.nativeChinese ? "切换到客户端将停止正在运行的后台服务。确定继续吗？" : "Switching to Client will stop the running daemon. Are you sure?")
                : (AppLocalePreference.shared.nativeChinese ? "切换到本机会断开远程运行时的连接。确定继续吗？" : "Switching to Home will disconnect from the remote runtime. Are you sure?")
        } else {
            body = appDelegate.connectionRole == .home
                ? (AppLocalePreference.shared.nativeChinese ? "切换到客户端模式？你将连接到远程 HappyRanch 运行时。" : "Switch to Client mode? You will connect to a remote HappyRanch runtime.")
                : (AppLocalePreference.shared.nativeChinese ? "切换到本机模式？你将在本地运行 HappyRanch 后台服务。" : "Switch to Home mode? You will run the HappyRanch daemon locally.")
        }

        if confirmed(title, body) {
            executeRoleSwitch(from: appDelegate.connectionRole, in: appDelegate)
        }
    }
}
