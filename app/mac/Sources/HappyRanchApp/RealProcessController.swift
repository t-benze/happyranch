import Foundation
import HappyRanchSupervisor

/// Production implementation of ProcessControlling that wraps Foundation.Process.
/// Not Sendable-safe by construction (Process is not Sendable), but only accessed
/// from @MainActor via AppDelegate.
///
/// Foundation.Process is single-use: calling run() a second time after the
/// process has exited throws NSCocoaErrorDomain Code=3587.  This controller
/// therefore creates a fresh Process on every launch(), discarding any prior
/// instance.
///
/// The `terminationHandler` parameter is wired to Process.terminationHandler
/// BEFORE `process.run()`, making handler registration and launch atomic.
/// No exit callback can be dropped by a fast-exiting child.
final class RealProcessController: ProcessControlling, @unchecked Sendable {

    func launch(
        executableURL: URL,
        arguments: [String],
        currentDirectoryURL: URL?,
        environment: [String: String]?,
        terminationHandler: (@Sendable (any ProcessHandle) -> Void)?
    ) throws -> any ProcessHandle {
        let newProcess = Process()
        newProcess.executableURL = executableURL
        newProcess.arguments = arguments
        newProcess.currentDirectoryURL = currentDirectoryURL
        newProcess.environment = environment

        let handle = RealProcessHandle(process: newProcess)

        // Wire terminationHandler BEFORE run() so no exit can be dropped.
        // Each Process gets its own closure capturing the per-launch handle.
        newProcess.terminationHandler = { _ in
            terminationHandler?(handle)
        }

        try newProcess.run()
        return handle
    }
}

/// Per-launch handle wrapping a Foundation.Process.
/// Delivers the wrapped Process's terminationStatus, terminationReason,
/// processIdentifier, and isRunning.
final class RealProcessHandle: ProcessHandle, @unchecked Sendable {
    private let process: Process

    init(process: Process) {
        self.process = process
    }

    var processIdentifier: Int32 { process.processIdentifier }
    var isRunning: Bool { process.isRunning }
    var terminationStatus: Int32 { process.terminationStatus }
    var terminationReason: Process.TerminationReason { process.terminationReason }

    func terminate() {
        process.terminate()
    }
}
