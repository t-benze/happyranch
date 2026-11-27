import AppKit
import SwiftUI
import Foundation
import Darwin

/// Branch before constructing SwiftUI/AppDelegate: CLI never starts a daemon,
/// opens a connection window, or controls an existing GUI/CLI invocation.
public enum AppEntry {
    @MainActor
    public static func main() async {
        let arguments = Array(CommandLine.arguments.dropFirst())
        if arguments.first?.hasPrefix("--qualify-app-scheme") == true {
            guard arguments.first == "--qualify-app-scheme" else { exit(2) }
            AppSchemeQualification.run(arguments)
        }
        guard arguments.first?.hasPrefix("--connection-client") == true else {
            // Process-only system-menu language, derived from the durable store.
            // It never persists UserDefaults or changes language during this launch.
            var domain = UserDefaults.standard.volatileDomain(forName: UserDefaults.argumentDomain)
            domain["AppleLanguages"] = [AppLocalePreference.shared.launchLocale]
            UserDefaults.standard.setVolatileDomain(domain, forName: UserDefaults.argumentDomain)
            HappyRanchApp.main()
            return
        }
        guard arguments.count == 2, arguments[0] == "--connection-client", ConnectionCLI.actions.contains(arguments[1]) else {
            FileHandle.standardError.write(Data("{\"code\":\"invalidGrammar\",\"effect\":\"none\"}\n".utf8))
            exit(2)
        }
        // Security retains its normal login-Keychain unlock/consent UI. This
        // accessory process owns no SwiftUI scene or AppDelegate lifecycle.
        _ = NSApplication.shared
        NSApp.setActivationPolicy(.accessory)
        signal(SIGPIPE, SIG_IGN) // output loss must still run native cleanup
        let client = ConnectionCLI()
        let operation = Task { await client.execute(arguments[1]) }
        let signals = [SIGINT, SIGTERM].map { number in
            signal(number, SIG_IGN)
            let source = DispatchSource.makeSignalSource(signal: number, queue: .main)
            source.setEventHandler { operation.cancel() }
            source.resume()
            return source
        }
        // Yield MainActor so the CLI operation and cancellation can run.
        let exitCode = await operation.value
        for source in signals { source.cancel() }
        exit(exitCode)
    }
}
