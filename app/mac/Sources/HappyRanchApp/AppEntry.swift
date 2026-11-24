import AppKit
import SwiftUI
import Foundation
import Darwin

/// Branch before constructing SwiftUI/AppDelegate: CLI never starts a daemon,
/// opens a connection window, or controls an existing GUI/CLI invocation.
@main
enum AppEntry {
    @MainActor
    static func main() {
        let arguments = Array(CommandLine.arguments.dropFirst())
        if arguments.first?.hasPrefix("--qualify-app-scheme") == true {
            guard arguments.first == "--qualify-app-scheme" else { exit(2) }
            AppSchemeQualification.run(arguments)
        }
        guard arguments.first?.hasPrefix("--connection-client") == true else {
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
        var exitCode: Int32?
        let operation = Task { exitCode = await client.execute(arguments[1]) }
        let signals = [SIGINT, SIGTERM].map { number in
            signal(number, SIG_IGN)
            let source = DispatchSource.makeSignalSource(signal: number, queue: .main)
            source.setEventHandler { operation.cancel() }
            source.resume()
            return source
        }
        while exitCode == nil {
            RunLoop.current.run(until: Date().addingTimeInterval(0.05))
        }
        for source in signals { source.cancel() }
        exit(exitCode ?? 1)
    }
}
