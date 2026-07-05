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
            handle.stopAsyncDrain()
            handle.drainRemainingPipes()
            terminationHandler?(handle)
        }

        try newProcess.run()

        // Start async pipe draining immediately after launch so the child
        // never blocks on a full pipe buffer (FINDING 3 fix).
        handle.startAsyncDrain()

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

    // MARK: - Async pipe drain (FINDING 3 fix)

    /// Start asynchronous draining of stdout and stderr pipes.
    /// Uses readabilityHandler to read data as it arrives so the child
    /// never blocks on a full pipe buffer. Data is appended into a
    /// bounded 64KB buffer under lock.
    func startAsyncDrain() {
        stdoutPipe.fileHandleForReading.readabilityHandler = { [weak self] fh in
            let data = fh.availableData
            guard !data.isEmpty else { return }
            self?.appendStdout(data)
        }
        stderrPipe.fileHandleForReading.readabilityHandler = { [weak self] fh in
            let data = fh.availableData
            guard !data.isEmpty else { return }
            self?.appendStderr(data)
        }
    }

    /// Stop async draining by removing readability handlers.
    /// Called from terminationHandler before draining remaining data.
    func stopAsyncDrain() {
        stdoutPipe.fileHandleForReading.readabilityHandler = nil
        stderrPipe.fileHandleForReading.readabilityHandler = nil
    }

    /// Drain any remaining data from the pipes after the process exits.
    /// Complements the async drain for data that arrived after the last
    /// readabilityHandler invocation but before the handler was removed.
    func drainRemainingPipes() {
        let remainingStdout = stdoutPipe.fileHandleForReading.readDataToEndOfFile()
        if !remainingStdout.isEmpty {
            appendStdout(remainingStdout)
        }
        let remainingStderr = stderrPipe.fileHandleForReading.readDataToEndOfFile()
        if !remainingStderr.isEmpty {
            appendStderr(remainingStderr)
        }
    }

    private func appendStdout(_ data: Data) {
        captureLock.lock()
        defer { captureLock.unlock() }
        let remaining = Self.maxCaptureBytes - (_capturedStandardOutput?.utf8.count ?? 0)
        guard remaining > 0 else { return }
        let chunk = data.prefix(remaining)
        if let str = String(data: chunk, encoding: .utf8) {
            if _capturedStandardOutput == nil {
                _capturedStandardOutput = str
            } else {
                _capturedStandardOutput! += str
            }
        }
    }

    private func appendStderr(_ data: Data) {
        captureLock.lock()
        defer { captureLock.unlock() }
        let remaining = Self.maxCaptureBytes - (_capturedStandardError?.utf8.count ?? 0)
        guard remaining > 0 else { return }
        let chunk = data.prefix(remaining)
        if let str = String(data: chunk, encoding: .utf8) {
            if _capturedStandardError == nil {
                _capturedStandardError = str
            } else {
                _capturedStandardError! += str
            }
        }
    }
}
