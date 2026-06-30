import Foundation
import HappyRanchSupervisor

/// Production implementation of ProcessControlling that wraps Foundation.Process.
/// Not Sendable-safe by construction (Process is not Sendable), but only accessed
/// from @MainActor via AppDelegate.
///
/// Foundation.Process is single-use: calling run() a second time after the
/// process has exited throws NSCocoaErrorDomain Code=3587.  This controller
/// therefore creates a fresh Process on every launch(), discarding any prior
/// instance.  The caller's terminationHandler closure is preserved across
/// launches and wired to each new Process so that AppDelegate receives exactly
/// one exit callback per managed daemon lifecycle.
final class RealProcessController: ProcessControlling, @unchecked Sendable {
    private var process: Process?
    private var _terminationHandler: (@Sendable (any ProcessControlling) -> Void)?

    var isRunning: Bool { process?.isRunning ?? false }
    var processIdentifier: Int32 { process?.processIdentifier ?? 0 }
    var terminationStatus: Int32 { process?.terminationStatus ?? 0 }
    var terminationReason: Process.TerminationReason { process?.terminationReason ?? .exit }

    var terminationHandler: (@Sendable (any ProcessControlling) -> Void)? {
        get { _terminationHandler }
        set { _terminationHandler = newValue }
    }

    func launch(
        executableURL: URL,
        arguments: [String],
        currentDirectoryURL: URL?,
        environment: [String: String]?
    ) throws {
        // Create a fresh Process every launch — Foundation.Process is single-use.
        let newProcess = Process()
        newProcess.executableURL = executableURL
        newProcess.arguments = arguments
        newProcess.currentDirectoryURL = currentDirectoryURL
        newProcess.environment = environment
        newProcess.terminationHandler = { [weak self] _ in
            guard let self else { return }
            self._terminationHandler?(self)
        }
        self.process = newProcess
        try newProcess.run()
    }

    func terminate() {
        process?.terminate()
    }
}
