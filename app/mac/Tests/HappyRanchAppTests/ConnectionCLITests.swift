import Foundation
import Darwin
import Testing
import HappyRanchSupervisor
@testable import HappyRanchApp

private actor CLIStore: SavedHomeCredentialStore {
    let discovery: SavedHomeDiscovery
    private(set) var reads = 0
    init(_ discovery: SavedHomeDiscovery = SavedHomeDiscovery(credentials: [])) { self.discovery = discovery }
    func savedHomes() async throws -> SavedHomeDiscovery { reads += 1; return discovery }
    func load(binding: SavedDeviceBinding, trust: SavedHomeTrust) async throws -> SavedDeviceCredential {
        reads += 1; throw SavedAccessFailure.unavailable
    }
    func save(_ credential: SavedDeviceCredential) async throws { reads += 1; throw SavedAccessFailure.unavailable }
    func delete(binding: SavedDeviceBinding) async throws { reads += 1; throw SavedAccessFailure.unavailable }
    func refreshAddress(from credential: SavedDeviceCredential, to trust: SavedHomeTrust) async throws -> SavedDeviceCredential {
        reads += 1; throw SavedAccessFailure.unavailable
    }
}

private enum TerminalFixtureError: Error { case systemCall, timeout }

/// A private PTY, never the test runner's controlling terminal. All input is synthetic.
@MainActor
private final class CLITerminalPTY {
    let master: Int32
    let slave: Int32
    let original: termios

    init() throws {
        var master: Int32 = -1
        var slave: Int32 = -1
        guard openpty(&master, &slave, nil, nil, nil) == 0 else { throw TerminalFixtureError.systemCall }
        do {
            var mode = termios()
            guard tcgetattr(slave, &mode) == 0 else { throw TerminalFixtureError.systemCall }
            // Start with both echo flags enabled so restoration cannot pass vacuously.
            mode.c_lflag |= tcflag_t(ECHO | ECHONL | ICANON)
            mode.c_iflag &= ~tcflag_t(ICRNL | INLCR | IGNCR)
            guard tcsetattr(slave, TCSAFLUSH, &mode) == 0 else { throw TerminalFixtureError.systemCall }
            for fd in [master, slave] {
                let flags = fcntl(fd, F_GETFL)
                guard flags >= 0, fcntl(fd, F_SETFL, flags | O_NONBLOCK) == 0 else {
                    throw TerminalFixtureError.systemCall
                }
            }
            self.master = master
            self.slave = slave
            self.original = mode
        } catch {
            Darwin.close(master)
            Darwin.close(slave)
            throw error
        }
    }

    deinit { Darwin.close(master); Darwin.close(slave) }

    func terminal(readTimeout: Duration = .seconds(5)) throws -> ConnectionTerminal {
        try ConnectionTerminal(descriptor: Darwin.dup(slave), readTimeout: readTimeout)
    }

    func mode() throws -> termios {
        var mode = termios()
        guard tcgetattr(slave, &mode) == 0 else { throw TerminalFixtureError.systemCall }
        return mode
    }

    func expectRestored() throws {
        let actual = try mode()
        #expect(actual.c_lflag == original.c_lflag)
        #expect(actual.c_iflag == original.c_iflag)
        #expect(actual.c_oflag == original.c_oflag)
        #expect(actual.c_cflag == original.c_cflag)
    }

    func availableOutput() throws -> [UInt8] {
        var output = [UInt8]()
        var buffer = [UInt8](repeating: 0, count: 1024)
        while true {
            let count = buffer.withUnsafeMutableBytes { Darwin.read(master, $0.baseAddress, $0.count) }
            if count > 0 { output.append(contentsOf: buffer.prefix(count)); continue }
            if count == 0 { return output }
            if errno == EINTR { continue }
            if errno == EAGAIN { return output }
            throw TerminalFixtureError.systemCall
        }
    }

    func awaitPrompt() async throws {
        let deadline = ContinuousClock.now.advanced(by: .seconds(5))
        var output = [UInt8]()
        while ContinuousClock.now < deadline {
            output += try availableOutput()
            if output.contains(10) {
                #expect(String(decoding: output, as: UTF8.self).contains("Paste invite (hidden):"))
                let active = try mode()
                #expect(active.c_lflag & tcflag_t(ECHO | ECHONL | ICANON) == 0)
                return
            }
            try await Task.sleep(for: .milliseconds(5))
        }
        throw TerminalFixtureError.timeout
    }

    func type(_ input: [UInt8]) async throws {
        let deadline = ContinuousClock.now.advanced(by: .seconds(5))
        var offset = 0
        while offset < input.count, ContinuousClock.now < deadline {
            let count = input.withUnsafeBytes {
                Darwin.write(master, $0.baseAddress!.advanced(by: offset), $0.count - offset)
            }
            if count > 0 { offset += count; continue }
            if count < 0, errno == EINTR { continue }
            guard count < 0, errno == EAGAIN else { throw TerminalFixtureError.systemCall }
            try await Task.sleep(for: .milliseconds(5))
        }
        guard offset == input.count else { throw TerminalFixtureError.timeout }
    }
}

private final class CLITestBundleMarker: NSObject {}

/// script owns the isolated controlling PTY and relays stdin to its master.
/// The observer opens /dev/tty with production flags, then execs the built CLI.
/// No invite is passed through argv, environment, or a typescript file.
@MainActor
private final class CLIControllingTerminalChild {
    let process = Process()
    private let input = Pipe()
    private let output = Pipe()
    private var transcript = Data()

    init(app: URL? = nil) throws {
        let observer = #"""
        import ctypes, json, os, signal, sys, termios
        signal.alarm(10)  # survives exec; bounds the child even if intake stalls
        fd = os.open('/dev/tty', os.O_RDWR | os.O_CLOEXEC | os.O_NONBLOCK)
        original = termios.tcgetattr(fd)
        original[3] |= termios.ECHO | termios.ECHONL | termios.ICANON
        termios.tcsetattr(fd, termios.TCSAFLUSH, original)
        mode = termios.tcgetattr(fd)
        mode[3] &= ~(termios.ICANON | termios.ECHO | termios.ECHONL)
        class PollFD(ctypes.Structure):
            _fields_ = [('fd', ctypes.c_int), ('events', ctypes.c_short),
                        ('revents', ctypes.c_short)]
        libc = ctypes.CDLL(None, use_errno=True)
        libc.poll.argtypes = [ctypes.POINTER(PollFD), ctypes.c_uint, ctypes.c_int]
        libc.poll.restype = ctypes.c_int
        try:
            termios.tcsetattr(fd, termios.TCSAFLUSH, mode)
            descriptor = PollFD(fd, 1, 0)  # Darwin POLLIN
            ctypes.set_errno(0)
            ready = libc.poll(ctypes.byref(descriptor), 1, 100)
            error = ctypes.get_errno()
            print('TTY_POLL ' + json.dumps(dict(ready=ready, revents=descriptor.revents,
                  errno=error, tty=int(os.isatty(fd)),
                  foreground=int(os.tcgetpgrp(fd) == os.getpgrp()))), flush=True)
        finally:
            termios.tcsetattr(fd, termios.TCSAFLUSH, original)
            os.close(fd)
        if len(sys.argv) > 1:
            os.execv(sys.argv[1], [sys.argv[1], '--connection-client', 'accept-invite'])
        """#
        let reader = output.fileHandleForReading.fileDescriptor
        let flags = fcntl(reader, F_GETFL)
        guard flags >= 0, fcntl(reader, F_SETFL, flags | O_NONBLOCK) == 0,
              fcntl(input.fileHandleForWriting.fileDescriptor, F_SETNOSIGPIPE, 1) == 0 else {
            throw TerminalFixtureError.systemCall
        }
        process.executableURL = URL(fileURLWithPath: "/usr/bin/script")
        process.arguments = ["-q", "/dev/null", "/usr/bin/python3", "-I", "-B", "-c", observer]
            + (app.map { [$0.path] } ?? [])
        process.standardInput = input
        process.standardOutput = output
        process.standardError = output
        try process.run()
    }

    func stop() {
        if process.isRunning { process.terminate() }
        try? input.fileHandleForWriting.close()
        try? output.fileHandleForReading.close()
    }

    private func drain() throws -> String {
        var buffer = [UInt8](repeating: 0, count: 1024)
        while transcript.count < 16_384 {
            let count = buffer.withUnsafeMutableBytes {
                Darwin.read(output.fileHandleForReading.fileDescriptor, $0.baseAddress, $0.count)
            }
            if count > 0 { transcript.append(contentsOf: buffer.prefix(count)); continue }
            if count == 0 || (count < 0 && errno == EAGAIN) {
                return String(decoding: transcript, as: UTF8.self)
            }
            if errno == EINTR { continue }
            throw TerminalFixtureError.systemCall
        }
        throw TerminalFixtureError.systemCall
    }

    func awaitPrompt() async throws -> Bool {
        let deadline = ContinuousClock.now.advanced(by: .seconds(15))
        while ContinuousClock.now < deadline {
            if try drain().replacingOccurrences(of: "\r\n", with: "\n").contains("Paste invite (hidden):\n") {
                return true
            }
            if !process.isRunning { return false }
            try await Task.sleep(for: .milliseconds(5))
        }
        throw TerminalFixtureError.timeout
    }

    func type(_ value: String) throws {
        let bytes = Array((value + "\n").utf8)
        while true {
            let count = bytes.withUnsafeBytes {
                Darwin.write(input.fileHandleForWriting.fileDescriptor, $0.baseAddress, $0.count)
            }
            if count == bytes.count { return }
            // Old poll can exit immediately after the prompt. Still collect
            // its JSON so invalidInvite vs unavailable is the regression check.
            if count < 0, errno == EPIPE { return }
            if count < 0, errno == EINTR { continue }
            throw TerminalFixtureError.systemCall
        }
    }

    func finish() async throws -> [String] {
        let deadline = ContinuousClock.now.advanced(by: .seconds(15))
        while process.isRunning, ContinuousClock.now < deadline {
            _ = try drain()
            try await Task.sleep(for: .milliseconds(5))
        }
        guard !process.isRunning else { throw TerminalFixtureError.timeout }
        var lines = try drain().replacingOccurrences(of: "\r\n", with: "\n").components(separatedBy: "\n")
        if lines.last == "" { lines.removeLast() }
        return lines // preserve extra blank lines so ECHONL cannot pass unnoticed
    }

    static func pollObservation(_ lines: [String]) throws -> [String: Int] {
        let line = try #require(lines.first { $0.hasPrefix("TTY_POLL ") })
        let observation = try #require(try JSONSerialization.jsonObject(with:
            Data(line.dropFirst("TTY_POLL ".count).utf8)) as? [String: Int])
        print("Controlling /dev/tty poll observation: \(observation)")
        #expect(observation["tty"] == 1)
        #expect(observation["foreground"] == 1)
        return observation
    }
}

@Suite("Mac foreground connection CLI")
@MainActor
struct ConnectionCLITests {
    @Test("Darwin poll characterizes the controlling /dev/tty path")
    func pollControllingTerminalCharacterization() async throws {
        let child = try CLIControllingTerminalChild()
        defer { child.stop() }
        let observation = try CLIControllingTerminalChild.pollObservation(await child.finish())
        #expect(child.process.terminationReason == .exit)
        #expect(child.process.terminationStatus == 0)
        #expect(observation["ready"] == 1, "Observed /dev/tty poll: \(observation)")
        let revents = try #require(observation["revents"])
        #expect(revents & Int(POLLNVAL) != 0, "Observed /dev/tty poll: \(observation)")
    }

    @Test("Built CLI reads a hidden malformed invite from controlling /dev/tty")
    func hiddenInviteControllingTerminalCLI() async throws {
        let app = Bundle(for: CLITestBundleMarker.self).bundleURL.deletingLastPathComponent()
            .appendingPathComponent("HappyRanchApp")
        try #require(FileManager.default.isExecutableFile(atPath: app.path), "Missing built CLI: \(app.path)")
        let child = try CLIControllingTerminalChild(app: app)
        defer { child.stop() }
        let prompted = try await child.awaitPrompt()
        let malformed = "hrinvite2:malformed-ctty-canary!"
        if prompted { try child.type(malformed) }
        let lines = try await child.finish()
        _ = try CLIControllingTerminalChild.pollObservation(lines)
        #expect(child.process.terminationReason == .exit)
        #expect(child.process.terminationStatus == 1)
        #expect(prompted)
        #expect(!lines.joined().contains(malformed))
        // Only the observer, production prompt and closed CLI result may be
        // visible on the master. Partial echo is unexpected output too.
        try #require(lines.count == 3)
        #expect(lines[1] == "Paste invite (hidden):")
        let result = try #require(try JSONSerialization.jsonObject(with: Data(lines[2].utf8)) as? [String: String])
        #expect(result["code"] == "invalidInvite")
        #expect(result == ["code": "invalidInvite", "effect": "none"])
    }

    @Test("Hidden PTY input accepts the full invite byte limit without echo and restores mode")
    func hiddenInvitePTY() async throws {
        let pty = try CLITerminalPTY()
        let terminal = try pty.terminal()
        let operation = Task { try await terminal.read("Paste invite (hidden):", hidden: true, limit: 5500) }
        defer { operation.cancel() }
        try await pty.awaitPrompt()
        let invite = "hrinvite2:" + String(repeating: "a", count: 5490)
        try await pty.type(Array((invite + "\n").utf8))
        let received = try await operation.value
        #expect(received == invite)
        #expect(try pty.availableOutput().isEmpty)
        try pty.expectRestored()
    }

    @Test("PTY editing handles both backspaces, empty input and CR/LF", arguments: [UInt8(10), UInt8(13)])
    func terminalEditingPTY(ending: UInt8) async throws {
        let pty = try CLITerminalPTY()
        let terminal = try pty.terminal()
        let operation = Task { try await terminal.read("Paste invite (hidden):", hidden: true) }
        defer { operation.cancel() }
        try await pty.awaitPrompt()
        try await pty.type([8, 127, 97, 98, 99, 8, 127, 100, ending])
        #expect(try await operation.value == "ad")
        #expect(try pty.availableOutput().isEmpty)
        try pty.expectRestored()
    }

    @Test("Cancellation ends an idle PTY read promptly and restores echo")
    func cancelledReadPTY() async throws {
        let pty = try CLITerminalPTY()
        let terminal = try pty.terminal()
        let operation = Task { try await terminal.read("Paste invite (hidden):", hidden: true) }
        defer { operation.cancel() }
        try await pty.awaitPrompt()
        try await pty.type(Array("partial".utf8))
        let cancelledAt = ContinuousClock.now
        operation.cancel()
        do { _ = try await operation.value; Issue.record("Terminal read ignored cancellation") }
        catch { #expect(error is CancellationError) }
        #expect(cancelledAt.duration(to: .now) < .seconds(2))
        #expect(try pty.availableOutput().isEmpty)
        try pty.expectRestored()
    }

    @Test("A monotonic deadline ends PTY input and restores echo")
    func deadlineReadPTY() async throws {
        let pty = try CLITerminalPTY()
        let terminal = try pty.terminal(readTimeout: .milliseconds(200))
        let startedAt = ContinuousClock.now
        let operation = Task { try await terminal.read("Paste invite (hidden):", hidden: true) }
        defer { operation.cancel() }
        try await pty.awaitPrompt()
        try await pty.type(Array("partial".utf8))
        do { _ = try await operation.value; Issue.record("Terminal read ignored deadline") }
        catch { #expect(error is CancellationError) }
        let elapsed = startedAt.duration(to: .now)
        #expect(elapsed >= .milliseconds(200))
        #expect(elapsed < .seconds(2))
        #expect(try pty.availableOutput().isEmpty)
        try pty.expectRestored()
    }

    @Test("Ctrl-D cancels a PTY read and restores echo")
    func endOfInputPTY() async throws {
        let pty = try CLITerminalPTY()
        let terminal = try pty.terminal()
        let operation = Task { try await terminal.read("Paste invite (hidden):", hidden: true) }
        defer { operation.cancel() }
        try await pty.awaitPrompt()
        try await pty.type(Array("partial".utf8) + [4])
        do { _ = try await operation.value; Issue.record("Terminal read accepted Ctrl-D") }
        catch { #expect(error is CancellationError) }
        #expect(try pty.availableOutput().isEmpty)
        try pty.expectRestored()
    }

    @Test("PTY byte overflow and invalid UTF-8 fail closed and restore echo", arguments: [false, true])
    func invalidInputPTY(invalidUTF8: Bool) async throws {
        let pty = try CLITerminalPTY()
        let terminal = try pty.terminal()
        let operation = Task { try await terminal.read("Paste invite (hidden):", hidden: true, limit: 5500) }
        defer { operation.cancel() }
        try await pty.awaitPrompt()
        try await pty.type(invalidUTF8 ? [255, 10] : [UInt8](repeating: 97, count: 5501))
        do { _ = try await operation.value; Issue.record("Terminal read accepted invalid input") }
        catch { #expect(error as? ConnectionError == ConnectionError(.invalidInvite)) }
        #expect(try pty.availableOutput().isEmpty)
        try pty.expectRestored()
    }

    @Test("Separate invocations own no session and do not touch custody or transport")
    func foreignActions() async throws {
        let store = CLIStore()
        let cli = ConnectionCLI(store: store, makeSession: { fatalError("foreign invocation started transport") })
        let status = try await cli.run("status")
        #expect(status as? [String: String] == ["state": "notOwned", "effect": "none", "observation": "unavailable"])
        for action in ["disconnect", "cancel-attempt", "retry", "retry-save", "health"] {
            let result = try await cli.run(action)
            #expect(result as? [String: String] == ["code": "unavailable", "state": "notOwned", "effect": "none", "recovery": "owningProcess"])
        }
        #expect(await store.reads == 0)
    }

    private func credential(id: UUID? = UUID()) throws -> SavedDeviceCredential {
        // Synthetic protected DTO; parser/CA verification is owned by invite tests.
        let trust = SavedHomeTrust(homeID: "HOME-CANARY", controlPlaneID: "CONTROL-CANARY", connectorID: "CONNECTOR-CANARY",
            controlURL: "https://control.happyranch.invalid:8443", dialIP: "192.168.1.40", homeEndpoint: "100.64.0.10:443",
            caDER: Data("CA-CANARY".utf8), caSHA256: "FINGERPRINT-CANARY")
        return try SavedDeviceCredential(binding: SavedDeviceBinding(homeID: trust.homeID,
            controlPlaneID: trust.controlPlaneID, stableNodeID: "NODE-CANARY"), trust: trust,
            deliveredBearer: Data("BEARER-CANARY".utf8), networkProfileID: id)
    }

    @Test("Saved listing uses generic labels, opaque refs and explicit partial discovery")
    func savedProjection() async throws {
        let record = try credential()
        let store = CLIStore(SavedHomeDiscovery(credentials: [record], failures: [.denied]))
        let cli = ConnectionCLI(store: store, makeSession: { fatalError("list started transport") })
        let result = try await cli.run("saved-computers")
        #expect(result["savedAccess"] as? String == "saved")
        #expect(result["observation"] as? String == "stale")
        let rows = try #require(result["computers"] as? [[String: String]])
        #expect(rows.count == 1)
        #expect(Set(rows[0].keys) == ["computerRef", "label"])
        #expect(rows[0]["label"] == "Saved computer 1")
        let reference = try #require(rows[0]["computerRef"])
        #expect(ConnectionRequest.identifier(reference))
        let serialized = String(decoding: try JSONSerialization.data(withJSONObject: result), as: UTF8.self)
        for canary in ["HOME-CANARY", "CONTROL-CANARY", "CONNECTOR-CANARY", "NODE-CANARY", "CA-CANARY",
                       "FINGERPRINT-CANARY", "BEARER-CANARY", "192.168.1.40", "100.64.0.10", record.networkProfileID!.uuidString] {
            #expect(!serialized.contains(canary))
        }
        #expect(await store.reads == 1)
        let unavailableCLI = ConnectionCLI(store: CLIStore(SavedHomeDiscovery(credentials: [], failures: [.locked])))
        let unavailable = try await unavailableCLI.run("saved-computers")
        #expect(unavailable["savedAccess"] as? String == "unknown")
        #expect(unavailable["observation"] as? String == "stale")
    }

    @Test("Oversized, missing-profile and duplicate-profile discovery cannot select a target")
    func savedBounds() async throws {
        let records = try (0..<32).map { _ in try credential() }
        let bounded = ConnectionCLI(store: CLIStore(SavedHomeDiscovery(credentials: records)))
        let result = try await bounded.run("saved-computers")
        #expect((result["computers"] as? [[String: String]])?.count == 32)
        for invalid in [records + [try credential()], [try credential(id: nil)], [records[0], records[0]]] {
            let cli = ConnectionCLI(store: CLIStore(SavedHomeDiscovery(credentials: invalid)))
            do { _ = try await cli.run("saved-computers"); Issue.record("Unsafe discovery was projected") }
            catch { #expect(error as? ConnectionError == ConnectionError(.unavailable)) }
        }
    }

    @Test("Cancellation precedes invite intake and cannot start custody or transport")
    func cancellationBeforeIntake() async throws {
        let store = CLIStore()
        let cli = ConnectionCLI(store: store, makeSession: { fatalError("cancelled invocation started transport") })
        let operation = Task { _ = try await cli.run("accept-invite") }
        operation.cancel()
        do { _ = try await operation.value; Issue.record("Cancellation was ignored") }
        catch { #expect(error is CancellationError) }
        #expect(await store.reads == 0)
    }
}
