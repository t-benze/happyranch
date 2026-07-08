import SwiftUI
import AppKit
import HappyRanchSupervisor

@main
struct HappyRanchApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var appDelegate

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(appDelegate)
                .frame(minWidth: 900, minHeight: 600)
        }
        .windowResizability(.contentMinSize)
        .commands {
            CommandGroup(replacing: .newItem) {}

            CommandMenu("Daemon") {
                Button("Start Daemon") {
                    appDelegate.startDaemon()
                }
                .disabled(!appDelegate.canStart)

                Button("Stop Daemon") {
                    appDelegate.stopDaemonWithConfirmation()
                }
                .disabled(!appDelegate.canStop)

                Button("Restart Daemon") {
                    appDelegate.restartDaemon()
                }
                .disabled(!appDelegate.canRestart)

                Divider()

                Button("Show Remote Access…") {
                    appDelegate.showRemoteAccess = true
                }

                Divider()

                Button(appDelegate.switchRoleMenuLabel) {
                    confirmAndSwitchRole(appDelegate)
                }

                Divider()

                Button("Show Diagnostics…") {
                    appDelegate.showDiagnostics = true
                }

                Divider()

                // Status line — disabled to appear as a non-interactive indicator
                Text("Status: \(appDelegate.stateText)")
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

    /// Present an NSAlert confirmation before switching roles.
    /// Called from the "Switch to Client…" / "Switch to Home…" menu item.
    private func confirmAndSwitchRole(_ appDelegate: AppDelegate) {
        let alert = NSAlert()
        alert.messageText = "Change Connection Role"

        if appDelegate.roleSwitchRequiresTeardown {
            alert.informativeText = appDelegate.connectionRole == .home
                ? "Switching to Client will stop the running daemon. Are you sure?"
                : "Switching to Home will disconnect from the remote runtime. Are you sure?"
        } else {
            alert.informativeText = appDelegate.connectionRole == .home
                ? "Switch to Client mode? You will connect to a remote HappyRanch runtime."
                : "Switch to Home mode? You will run the HappyRanch daemon locally."
        }

        alert.alertStyle = .warning
        alert.addButton(withTitle: "Switch")
        alert.addButton(withTitle: "Cancel")

        if alert.runModal() == .alertFirstButtonReturn {
            executeRoleSwitch(from: appDelegate.connectionRole, in: appDelegate)
        }
    }
}
