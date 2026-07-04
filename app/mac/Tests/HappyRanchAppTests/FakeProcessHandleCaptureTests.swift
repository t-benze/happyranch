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

    @Test("crash state + captured stderr/exit still surface after high-volume then crash")
    func crashAfterHighVolumeOutput() throws {
        let controller = RealProcessController()
        // Simulates: daemon writes lots of stdout, lots of stderr, then crashes
        let handle = try controller.launch(
            executableURL: URL(fileURLWithPath: "/bin/sh"),
            arguments: ["-c", "for i in $(seq 1 500); do echo \"log line $i\"; done; echo 'FATAL: port bind failed' >&2; exit 1"],
            currentDirectoryURL: nil,
            environment: nil,
            terminationHandler: nil
        )
        let proc = handle as! RealProcessHandle

        let deadline = Date().addingTimeInterval(10.0)
        while proc.isRunning && Date() < deadline {
            Thread.sleep(forTimeInterval: 0.1)
        }

        #expect(!proc.isRunning, "Process must exit after crash")
        #expect(proc.terminationStatus == 1, "Exit code must be 1")

        let stderr = proc.capturedStandardError ?? ""
        #expect(stderr.contains("port bind failed"),
                "Crash stderr must surface, got: \(stderr)")
    }
}
