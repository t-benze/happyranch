import Foundation
import Darwin

/// One helper owns one home/profile and its Go-global roots. All blocking work
/// runs on ClientBridge's engine worker, never on the main actor.
public final class HomeBridgeProcess: @unchecked Sendable {
    private let controller: any ProcessControlling
    private let executable: URL
    private let lock = NSLock()
    private var process: (any ProcessHandle)?
    private var input: FileHandle?
    private var directory: URL?
    private var trust: SavedHomeTrust?
    private var profile: String?
    private var hostname: String?
    private var descriptors: [Int32: Int32] = [:]
    private var cancelled = false
    private let owner = UUID()
    private static let ownerLock = NSLock()
    nonisolated(unsafe) private static var owners: [String: UUID] = [:]
    private static let handleLock = NSLock()
    nonisolated(unsafe) private static var nextHandle: Int32 = 1_000_000

    public init(controller: any ProcessControlling, executable: URL) {
        self.controller = controller
        self.executable = executable
    }

    /// Caller has validated/explicitly accepted this trust; a trust change always
    /// stops/reaps the old helper first. No key is retained after pipe handoff.
    public func start(trust: SavedHomeTrust, hostname: String, stateDirectory: String, joinKey: Data) throws {
        guard !lock.withLock({ cancelled }) else { throw ManagedNetworkError.canceled }
        if lock.withLock({ process?.isRunning == true && self.trust == trust && profile == stateDirectory && self.hostname == hostname }) {
            guard joinKey.isEmpty else { throw ManagedNetworkError.busy }
            return
        }
        try stop()
        try Self.ownerLock.withLock {
            guard Self.owners[stateDirectory] == nil else { throw ManagedNetworkError.busy }
            Self.owners[stateDirectory] = owner
        }
        lock.withLock { profile = stateDirectory }
        var template = Array("/tmp/hrbridge.XXXXXXXX".utf8CString)
        guard let path = mkdtemp(&template) else {
            try? stop(); throw ManagedNetworkError.stateFailure
        }
        let directory = URL(fileURLWithPath: String(cString: path), isDirectory: true)
        let socket = directory.appendingPathComponent("dial").path
        let incoming = Pipe(), outgoing = Pipe()
        lock.withLock { self.directory = directory; self.trust = trust; self.hostname = hostname }
        do {
            let handle = try controller.launchPiped(executableURL: executable,
                arguments: ["--home-bridge-v1"], environment: ["GODEBUG": "x509usefallbackroots=1"],
                input: incoming.fileHandleForReading, output: outgoing.fileHandleForWriting)
            try lock.withLock {
                process = handle
                guard !cancelled else { handle.terminate(); throw ManagedNetworkError.canceled }
                input = incoming.fileHandleForWriting
                self.directory = directory; self.trust = trust; profile = stateDirectory
            }
            try incoming.fileHandleForReading.close()
            try outgoing.fileHandleForWriting.close()
            guard fcntl(incoming.fileHandleForWriting.fileDescriptor, F_SETNOSIGPIPE, 1) == 0 else {
                throw ManagedNetworkError.unavailable
            }
            var payload = try JSONSerialization.data(withJSONObject: ["ca": trust.caDER.base64EncodedString(),
                "fingerprint": trust.caSHA256, "controlURL": trust.controlURL, "hostname": hostname,
                "stateDirectory": stateDirectory, "joinKey": String(decoding: joinKey, as: UTF8.self),
                "socketPath": socket, "target": trust.homeEndpoint])
            defer { payload.resetBytes(in: 0..<payload.count) }
            guard payload.count <= 16384 else { throw ManagedNetworkError.invalidConfiguration }
            var size = UInt32(payload.count).bigEndian
            try withUnsafeBytes(of: &size) { try incoming.fileHandleForWriting.write(contentsOf: Data($0)) }
            try incoming.fileHandleForWriting.write(contentsOf: payload)
            payload.resetBytes(in: 0..<payload.count)
            payload.removeAll()
            defer { try? outgoing.fileHandleForReading.close() }
            var response = Data()
            let deadline = Date().addingTimeInterval(130)
            while response.count < 8 && !response.contains(10) {
                let remaining = deadline.timeIntervalSinceNow
                guard remaining > 0 else { throw ManagedNetworkError.deadline }
                var fd = pollfd(fd: outgoing.fileHandleForReading.fileDescriptor, events: Int16(POLLIN), revents: 0)
                guard poll(&fd, 1, Int32(min(remaining * 1000, 130000))) > 0,
                      let byte = try outgoing.fileHandleForReading.read(upToCount: 1), !byte.isEmpty else {
                    throw ManagedNetworkError.unavailable
                }
                response.append(byte)
            }
            guard response == Data("0\n".utf8) else {
                let code = Int32(String(decoding: response, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)) ?? -14
                throw ManagedNetworkError.result(code)
            }
        } catch {
            do { try stop() } catch { throw ManagedNetworkError.stateFailure }
            // No library exception/input bytes cross the launcher boundary.
            throw (error as? ManagedNetworkError) ?? .unavailable
        }
    }

    /// Fixed target was sent once via the private pipe. This socket cannot ask
    /// the helper to dial another host, port, home, or trust mode.
    func dial(timeoutMs: Int32) -> Int32 {
        guard let path = lock.withLock({ process?.isRunning == true ? directory?.appendingPathComponent("dial").path : nil }) else { return -15 }
        let fd = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else { return -14 }
        var address = sockaddr_un()
        address.sun_family = sa_family_t(AF_UNIX)
        let bytes = Array(path.utf8CString)
        guard bytes.count <= MemoryLayout.size(ofValue: address.sun_path) else { Darwin.close(fd); return -10 }
        withUnsafeMutableBytes(of: &address.sun_path) { target in target.copyBytes(from: bytes.map { UInt8(bitPattern: $0) }) }
        var timeout = timeval(tv_sec: Int(min(max(timeoutMs, 1000), 10000) / 1000), tv_usec: 0)
        _ = setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, socklen_t(MemoryLayout<timeval>.size))
        var noSignal: Int32 = 1
        _ = setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &noSignal, socklen_t(MemoryLayout<Int32>.size))
        let connected = withUnsafePointer(to: &address) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) { Darwin.connect(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size)) }
        }
        var status: Int32 = 0
        var offset = 0
        let received = withUnsafeMutableBytes(of: &status) { buffer -> Bool in
            while offset < 4 {
                let n = Darwin.read(fd, buffer.baseAddress!.advanced(by: offset), 4 - offset)
                if n <= 0 { return false }; offset += n
            }
            return true
        }
        guard connected == 0, received, Int32(bigEndian: status) == 0 else {
            Darwin.close(fd); return received ? Int32(bigEndian: status) : -14
        }
        timeout = timeval(tv_sec: 0, tv_usec: 0)
        _ = setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, socklen_t(MemoryLayout<timeval>.size))
        let accepted = lock.withLock { () -> Bool in
            guard process?.isRunning == true else { return false }
            let id = Self.handleLock.withLock { () -> Int32 in
                Self.nextHandle += 1; return Self.nextHandle
            }
            descriptors[id] = fd; return true
        }
        if !accepted { Darwin.close(fd); return -12 }
        return lock.withLock { descriptors.first(where: { $0.value == fd })?.key ?? -12 }
    }

    func close(_ handle: Int32) {
        if let fd = lock.withLock({ descriptors.removeValue(forKey: handle) }) { _ = shutdown(fd, SHUT_RDWR); Darwin.close(fd) }
    }

    func read(_ handle: Int32, _ buffer: UnsafeMutablePointer<CChar>, _ count: Int32) -> Int32 {
        guard let fd = lock.withLock({ descriptors[handle].map { dup($0) } }), fd >= 0 else { return -12 }
        defer { Darwin.close(fd) }
        let n = Darwin.read(fd, buffer, Int(count))
        return n < 0 ? -14 : Int32(n)
    }

    func write(_ handle: Int32, _ buffer: UnsafePointer<CChar>, _ count: Int32) -> Int32 {
        guard let fd = lock.withLock({ descriptors[handle].map { dup($0) } }), fd >= 0 else { return -12 }
        defer { Darwin.close(fd) }
        let n = Darwin.write(fd, buffer, Int(count))
        return n < 0 ? -14 : Int32(n)
    }

    func cancel() {
        lock.withLock { cancelled = true; try? input?.close(); input = nil; if process?.isRunning == true { process?.terminate() } }
    }

    public func stop() throws {
        let handle = lock.withLock { () -> (any ProcessHandle)? in
            for fd in descriptors.values { _ = shutdown(fd, SHUT_RDWR); Darwin.close(fd) }
            descriptors.removeAll()
            try? input?.close(); input = nil
            return process
        }
        if let handle, handle.isRunning {
            handle.terminate()
            let deadline = Date().addingTimeInterval(2)
            while handle.isRunning && Date() < deadline { usleep(10000) }
            if handle.isRunning { _ = handle.forceKill() }
            let killDeadline = Date().addingTimeInterval(2)
            while handle.isRunning && Date() < killDeadline { usleep(10000) }
            guard !handle.isRunning else { throw ManagedNetworkError.stateFailure }
        }
        let old = lock.withLock { directory }
        if let old {
            let socket = old.appendingPathComponent("dial").path
            if unlink(socket) != 0 && errno != ENOENT { throw ManagedNetworkError.stateFailure }
            guard rmdir(old.path) == 0 else { throw ManagedNetworkError.stateFailure }
        }
        let oldProfile = lock.withLock { () -> String? in
            defer { process = nil; directory = nil; trust = nil; profile = nil; hostname = nil }
            return profile
        }
        if let oldProfile { Self.ownerLock.withLock { if Self.owners[oldProfile] == owner { Self.owners.removeValue(forKey: oldProfile) } } }
    }
}
