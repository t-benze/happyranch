import Foundation
import HappyRanchSupervisor

/// Error thrown by FakeProcessController when a single-use constraint is violated.
enum FakeProcessControllerError: Error, Equatable {
    case processAlreadyExited
}

// MARK: - FakeProcessHandle

/// Per-launch handle for the FakeProcessController.
/// Each FakeProcessHandle models ONE launched daemon process.
/// Once exited (via terminate() or simulateCrash()), the handle is
/// single-use: attempting to reuse it throws processAlreadyExited.
final class FakeProcessHandle: ProcessHandle, @unchecked Sendable {
    private var _isRunning: Bool
    private var _processIdentifier: Int32
    private var _terminationStatus: Int32
    private var _terminationReason: Process.TerminationReason
    private var _hasExited: Bool

    /// If set, calling launch() on this handle (i.e. reusing it) fires this
    /// block.  Used by the controller to detect reuse.
    var onRelaunchAttempt: (() -> Void)?

    /// Fired when terminate() is called.  The controller wires this to
    /// increment its call counter and fire the terminationHandler.
    var onTerminate: (() -> Void)?

    init(
        isRunning: Bool = true,
        processIdentifier: Int32 = 12345,
        terminationStatus: Int32 = 0,
        terminationReason: Process.TerminationReason = .exit,
        hasExited: Bool = false
    ) {
        self._isRunning = isRunning
        self._processIdentifier = processIdentifier
        self._terminationStatus = terminationStatus
        self._terminationReason = terminationReason
        self._hasExited = hasExited
    }

    var isRunning: Bool { _isRunning }
    var processIdentifier: Int32 { _processIdentifier }
    var terminationStatus: Int32 { _terminationStatus }
    var terminationReason: Process.TerminationReason { _terminationReason }

    func terminate() {
        _isRunning = false
        _hasExited = true
        _terminationReason = .exit
        _terminationStatus = 0
        onTerminate?()
    }

    /// Mark this handle as exited with a crash status.
    func simulateCrash(exitCode: Int32) {
        _isRunning = false
        _hasExited = true
        _terminationReason = .uncaughtSignal
        _terminationStatus = exitCode
    }

    var hasExited: Bool { _hasExited }

    /// Called by the controller when it attempts to reuse this handle.
    func assertNotExited() throws {
        if _hasExited {
            onRelaunchAttempt?()
            throw FakeProcessControllerError.processAlreadyExited
        }
    }
}

// MARK: - FakeProcessController

/// Test double for ProcessControlling that records calls and allows
/// test-driven control of process lifecycle.
///
/// Acts as a LONG-LIVED FACTORY: each `launch()` call creates a fresh
/// `FakeProcessHandle`.  The controller tracks call counts and stores
/// the active handle.  When `terminate()` is called on the active handle,
/// the controller fires its `terminationHandler` with that handle.
///
/// Models Foundation.Process single-use semantics: if a test attempts to
/// reuse an already-exited handle via the controller, it throws
/// `processAlreadyExited`.
final class FakeProcessController: ProcessControlling, @unchecked Sendable {
    /// The termination handler registered in the most recent launch() call.
    /// Stored so fireTermination(for:) and simulateCrash(exitCode:) can
    /// replay the handler for stale-callback regression tests.
    private var storedTerminationHandler: (@Sendable (any ProcessHandle) -> Void)?

    // Call counters for verification
    var launchCallCount = 0
    var terminateCallCount = 0

    // Last-launch arguments for verification
    var lastExecutableURL: URL?
    var lastArguments: [String]?
    var lastCurrentDirectoryURL: URL?
    var lastEnvironment: [String: String]?

    /// The most recent handle returned by launch().
    private(set) var activeHandle: FakeProcessHandle?

    /// All handles ever created by this controller.
    private var allHandles: [FakeProcessHandle] = []

    // MARK: - Configuration

    private var nextPID: Int32 = 12345

    /// When true, the next launch() call will fire the termination handler
    /// synchronously (simulating a daemon that exits immediately during
    /// startup) before returning the handle.
    var simulateImmediateExitOnNextLaunch = false
    var immediateExitCode: Int32 = 1

    /// Configure a custom PID for the next launch. Subsequent launches
    /// auto-increment.
    func setNextPID(_ pid: Int32) {
        nextPID = pid
    }

    // MARK: - ProcessControlling

    func launch(
        executableURL: URL,
        arguments: [String],
        currentDirectoryURL: URL?,
        environment: [String: String]?,
        terminationHandler: (@Sendable (any ProcessHandle) -> Void)?
    ) throws -> any ProcessHandle {
        let handle = FakeProcessHandle(
            isRunning: true,
            processIdentifier: nextPID,
            terminationStatus: 0,
            terminationReason: .exit,
            hasExited: false
        )
        self.storedTerminationHandler = terminationHandler
        // Wire handle.terminate() to increment call counter and fire handler.
        handle.onTerminate = { [weak self] in
            guard let self else { return }
            self.terminateCallCount += 1
            self.storedTerminationHandler?(handle)
        }

        nextPID += 1

        launchCallCount += 1
        lastExecutableURL = executableURL
        lastArguments = arguments
        lastCurrentDirectoryURL = currentDirectoryURL
        lastEnvironment = environment

        activeHandle = handle
        allHandles.append(handle)

        // Simulate an immediate daemon exit during launch (for regression testing
        // of the launch-then-register-termination-handler ordering race).
        if simulateImmediateExitOnNextLaunch {
            simulateImmediateExitOnNextLaunch = false
            handle.simulateCrash(exitCode: immediateExitCode)
            // Fire the termination handler synchronously.
            // In the OLD code (handler set AFTER launch returns), this is nil
            // and the exit is silently dropped → supervisor stays .starting.
            // In the NEW code (handler passed as launch parameter), this
            // fires the handler → supervisor reaches .crashed.
            self.storedTerminationHandler?(handle)
        }

        return handle
    }

    // MARK: - Test helpers

    /// Convenience: terminate the active handle.
    /// onTerminate already increments terminateCallCount and fires
    /// terminationHandler, so callers should NOT double-count.
    func terminateActive() {
        guard let handle = activeHandle else { return }
        handle.terminate()  // onTerminate wired by launch() handles the rest
    }

    /// Fire a termination callback for the given handle.
    /// Used for stale-callback regression tests: fire a handle's callback
    /// without going through terminateActive.
    func fireTermination(for handle: FakeProcessHandle) {
        storedTerminationHandler?(handle)
    }

    /// Simulate a crash for the active handle (via uncaughtSignal).
    func simulateCrash(exitCode: Int32) {
        guard let handle = activeHandle else { return }
        handle.simulateCrash(exitCode: exitCode)
        storedTerminationHandler?(handle)
    }

    /// All handles created by this controller, in order.
    var handles: [FakeProcessHandle] { allHandles }
}
