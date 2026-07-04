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
        let stdoutPipe = Pipe()
        let stderrPipe = Pipe()

        let newProcess = Process()
        newProcess.executableURL = executableURL
        newProcess.arguments = arguments
        newProcess.currentDirectoryURL = currentDirectoryURL
        newProcess.environment = environment
        newProcess.standardOutput = stdoutPipe
        newProcess.standardError = stderrPipe

        let handle = RealProcessHandle(process: newProcess, stdoutPipe: stdoutPipe, stderrPipe: stderrPipe)

        // Wire terminationHandler BEFORE run() so no exit can be dropped.
        // Each Process gets its own closure capturing the per-launch handle.
        newProcess.terminationHandler = { _ in
            handle.drainPipes()
            terminationHandler?(handle)
        }

        try newProcess.run()
        return handle
    }
}

/// Per-launch handle wrapping a Foundation.Process.
/// Delivers the wrapped Process's terminationStatus, terminationReason,
/// processIdentifier, isRunning, and captured stdout/stderr.
final class RealProcessHandle: ProcessHandle, @unchecked Sendable {
    private let process: Process
    private let stdoutPipe: Pipe
    private let stderrPipe: Pipe

    /// Bounded buffer for captured output: max 64 KB each.
    private static let maxCaptureBytes = 64 * 1024

    private var _capturedStandardOutput: String?
    private var _capturedStandardError: String?
    private let captureLock = NSLock()

    init(process: Process, stdoutPipe: Pipe, stderrPipe: Pipe) {
        self.process = process
        self.stdoutPipe = stdoutPipe
        self.stderrPipe = stderrPipe
    }

    var processIdentifier: Int32 { process.processIdentifier }
    var isRunning: Bool { process.isRunning }
    var terminationStatus: Int32 { process.terminationStatus }
    var terminationReason: Process.TerminationReason { process.terminationReason }

    var capturedStandardOutput: String? {
        captureLock.lock()
        defer { captureLock.unlock() }
        return _capturedStandardOutput
    }

    var capturedStandardError: String? {
        captureLock.lock()
        defer { captureLock.unlock() }
        return _capturedStandardError
    }

    func terminate() {
        process.terminate()
    }

    /// Drain the pipes into the captured strings. Called from terminationHandler.
    func drainPipes() {
        captureLock.lock()
        defer { captureLock.unlock() }
        _capturedStandardOutput = Self.drainPipe(stdoutPipe)
        _capturedStandardError = Self.drainPipe(stderrPipe)
    }

    private static func drainPipe(_ pipe: Pipe) -> String? {
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        guard !data.isEmpty else { return nil }
        let maxData = data.prefix(maxCaptureBytes)
        return String(data: maxData, encoding: .utf8)
    }
}
