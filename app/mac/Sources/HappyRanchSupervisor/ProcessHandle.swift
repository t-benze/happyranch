import Foundation

/// Represents ONE launched daemon process — a per-launch snapshot.
///
/// The controller (ProcessControlling) is a long-lived factory that creates
/// a fresh handle each launch. The handle carries the fields the exit path
/// needs: terminationStatus, terminationReason, processIdentifier, isRunning.
///
/// When a process exits, the terminationHandler delivers the EXITED handle
/// (the per-launch snapshot), so a stale callback from a prior launch reports
/// its OWN status — never the current/new process's values.
public protocol ProcessHandle: AnyObject, Sendable {
    /// The process identifier (valid only while running).
    var processIdentifier: Int32 { get }

    /// Whether the process is currently running.
    var isRunning: Bool { get }

    /// The exit code of a terminated process.
    var terminationStatus: Int32 { get }

    /// Why the process terminated.
    var terminationReason: Process.TerminationReason { get }

    /// Send SIGTERM to the process.
    func terminate()
}
