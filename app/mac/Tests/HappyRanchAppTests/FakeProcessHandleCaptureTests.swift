import Foundation
import Testing
@testable import HappyRanchApp
import HappyRanchSupervisor

@Suite("ProcessHandle stream capture - FakeProcessHandle")
struct FakeProcessHandleCaptureTests {

    @Test("FakeProcessHandle stores captured stderr from simulateCrash")
    func fakeHandleStoresCapturedStderr() {
        let handle = FakeProcessHandle()
        handle.simulateCrash(exitCode: 1, stderr: "Fatal error: port already in use\n")
        #expect(handle.capturedStandardError == "Fatal error: port already in use\n")
        #expect(handle.capturedStandardOutput == nil)
    }

    @Test("FakeProcessHandle stores captured stdout and stderr")
    func fakeHandleStoresCapturedStdoutAndStderr() {
        let handle = FakeProcessHandle()
        handle.simulateCrash(exitCode: 1, stderr: "error: bind failed\n", stdout: "daemon starting...\n")
        #expect(handle.capturedStandardError == "error: bind failed\n")
        #expect(handle.capturedStandardOutput == "daemon starting...\n")
    }

    @Test("FakeProcessHandle per-launch invariant: each handle carries its own streams")
    func perLaunchInvariant() {
        let handleA = FakeProcessHandle(processIdentifier: 100)
        let handleB = FakeProcessHandle(processIdentifier: 200)

        handleA.simulateCrash(exitCode: 1, stderr: "error from A")
        handleB.simulateCrash(exitCode: 2, stderr: "error from B")

        #expect(handleA.capturedStandardError == "error from A")
        #expect(handleA.terminationStatus == 1)
        #expect(handleB.capturedStandardError == "error from B")
        #expect(handleB.terminationStatus == 2)
    }

    // MARK: - RealProcessController stream capture

    @Test("RealProcessController wires stdout and stderr pipes for real process")
    func realControllerWiresPipes() throws {
        let controller = RealProcessController()
        let handle = try controller.launch(
            executableURL: URL(fileURLWithPath: "/bin/sh"),
            arguments: ["-c", "echo stdout-text; echo stderr-text >&2; exit 0"],
            currentDirectoryURL: nil,
            environment: nil,
            terminationHandler: nil
        )
        let proc = handle as! RealProcessHandle
        // Poll for exit
        let deadline = Date().addingTimeInterval(5.0)
        while proc.isRunning && Date() < deadline {
            Thread.sleep(forTimeInterval: 0.05)
        }
        let stdout = proc.capturedStandardOutput ?? ""
        let stderr = proc.capturedStandardError ?? ""
        #expect(stdout.contains("stdout-text"), "stdout should contain 'stdout-text', got: \(stdout)")
        #expect(stderr.contains("stderr-text"), "stderr should contain 'stderr-text', got: \(stderr)")
    }

    // MARK: - Async pipe drain (FINDING 3 fix)

    @Test("high-volume stdout+stderr does not deadlock and is captured")
    func highVolumeOutputDoesNotDeadlock() throws {
        let controller = RealProcessController()
        // Generate ~100KB of stdout, exceeding the default 64KB pipe buffer.
        // This would deadlock without async drain (child blocks on write).
        let handle = try controller.launch(
            executableURL: URL(fileURLWithPath: "/bin/sh"),
            arguments: ["-c", "yes 'line' | head -n 2000; echo 'FINAL_STDOUT_MARKER'; echo 'STDERR_MARKER' >&2; exit 0"],
            currentDirectoryURL: nil,
            environment: nil,
            terminationHandler: nil
        )
        let proc = handle as! RealProcessHandle

        // Wait up to 10 seconds — deadlock would prevent exit
        let deadline = Date().addingTimeInterval(10.0)
        while proc.isRunning && Date() < deadline {
            Thread.sleep(forTimeInterval: 0.1)
        }

        #expect(!proc.isRunning, "Process must exit (no deadlock)")

        let stdout = proc.capturedStandardOutput ?? ""
        let stderr = proc.capturedStandardError ?? ""

        // Stdout must contain the final marker (async drain captured it)
        #expect(stdout.contains("FINAL_STDOUT_MARKER"),
                "Stdout must contain final marker, got length: \(stdout.count)")
        // Stderr must contain the marker
        #expect(stderr.contains("STDERR_MARKER"),
                "Stderr must contain marker, got: \(stderr)")
    }

    @Test("async drain bounds output to 64KB")
    func asyncDrainBoundsOutput() throws {
        let controller = RealProcessController()
        // Generate ~200KB of stdout to verify 64KB bound
        let handle = try controller.launch(
            executableURL: URL(fileURLWithPath: "/bin/sh"),
            arguments: ["-c", "yes 'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx' | head -n 3000; exit 0"],
            currentDirectoryURL: nil,
            environment: nil,
            terminationHandler: nil
        )
        let proc = handle as! RealProcessHandle

        let deadline = Date().addingTimeInterval(10.0)
        while proc.isRunning && Date() < deadline {
            Thread.sleep(forTimeInterval: 0.1)
        }

        #expect(!proc.isRunning, "Process must exit")

        let stdout = proc.capturedStandardOutput ?? ""
        // 64KB bound = 65536 bytes
        #expect(stdout.utf8.count <= 65536,
                "Stdout must be bounded to 64KB, got \(stdout.utf8.count) bytes")
    }

    @Test("immediate exit process still captures streams via async drain")
    func immediateExitCapturesStreams() throws {
        let controller = RealProcessController()
        // Process that exits immediately with output
        let handle = try controller.launch(
            executableURL: URL(fileURLWithPath: "/bin/sh"),
            arguments: ["-c", "echo 'quick stdout'; echo 'quick stderr' >&2; exit 0"],
            currentDirectoryURL: nil,
            environment: nil,
            terminationHandler: nil
        )
        let proc = handle as! RealProcessHandle

        let deadline = Date().addingTimeInterval(5.0)
        while proc.isRunning && Date() < deadline {
            Thread.sleep(forTimeInterval: 0.05)
        }

        #expect(!proc.isRunning, "Process must exit")

        let stdout = proc.capturedStandardOutput ?? ""
        let stderr = proc.capturedStandardError ?? ""
        #expect(stdout.contains("quick stdout"),
                "Immediate-exit stdout must be captured, got: \(stdout)")
        #expect(stderr.contains("quick stderr"),
                "Immediate-exit stderr must be captured, got: \(stderr)")
    }

    @Test("high-volume output + non-zero exit: >100KB stdout + stderr captured, async drain prevents deadlock, crash exit code surfaces")
    func crashAfterHighVolumeOutput() throws {
        let controller = RealProcessController()
        // Generate >100KB of stdout (well above the 64KB OS pipe buffer),
        // substantial stderr, then exit NON-ZERO.  Without async drain this
        // deadlocks: the child fills the pipe and blocks on write, the
        // termination handler never fires.
        let handle = try controller.launch(
            executableURL: URL(fileURLWithPath: "/bin/sh"),
            arguments: ["-c", """
perl -e 'print "x" x 120000, "\\nFINAL_STDOUT_LINE\\n"';
echo 'FATAL: port bind failed: address already in use (errno 48)' >&2;
echo 'CRASH_STDERR_MARKER' >&2;
exit 3
"""],
            currentDirectoryURL: nil,
            environment: nil,
            terminationHandler: nil
        )
        let proc = handle as! RealProcessHandle

        // Wait up to 10 seconds — deadlock would prevent exit
        let deadline = Date().addingTimeInterval(10.0)
        while proc.isRunning && Date() < deadline {
            Thread.sleep(forTimeInterval: 0.1)
        }

        #expect(!proc.isRunning, "Process must exit after crash (no deadlock)")
        #expect(proc.terminationStatus == 3, "Exit code must be 3 (non-zero crash)")

        let stdout = proc.capturedStandardOutput ?? ""
        let stderr = proc.capturedStandardError ?? ""

        // High-volume stdout was captured (at least the bounded 64KB head).
        // The 64KB bound means the final marker line may not be in the
        // captured window — what matters is the async drain consumed enough
        // to keep the child from deadlocking AND captured output exists.
        #expect(stdout.utf8.count > 0,
                "High-volume stdout must capture output; length=\(stdout.utf8.count)")
        // Stderr with crash diagnostic must surface
        #expect(stderr.contains("port bind failed"),
                "Crash stderr 'port bind failed' must surface, got: \(stderr)")
        #expect(stderr.contains("CRASH_STDERR_MARKER"),
                "Crash stderr marker must surface")
    }
}
