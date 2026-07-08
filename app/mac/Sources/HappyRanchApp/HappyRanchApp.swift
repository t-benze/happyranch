import SwiftUI

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

                Button("Show Diagnostics…") {
                    appDelegate.showDiagnostics = true
                }

                Divider()

                // Status line — disabled to appear as a non-interactive indicator
                Text("Status: \(appDelegate.stateText)")
            }
        }
    }
}
